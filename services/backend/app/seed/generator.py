"""Deterministic synthetic seed data generator (Task 6).

``seed_demo`` builds the full demonstration dataset described in
``docs/architecture/backend-contracts.md`` section 9 and
`.superpowers/sdd/2026-09-17-linesense-build/task-6-brief.md`: the
identity org/factories/users, master data (customers, styles, BOMs,
materials, lines), ~100 orders with consistent capacity/inventory/quality
state, and the ``PO-DEMO-001`` walkthrough scenario (``app.seed.scenario``).

Determinism: every random choice is drawn from a single
``random.Random(rng_seed)`` instance seeded once at the top of
``seed_demo``; nothing here reads the wall clock or process entropy.  All
dates are computed relative to the caller-supplied ``anchor_date``.

Idempotency: if the ``demo-apparel`` organization already exists,
``seed_demo`` performs no writes and returns ``SeedSummary(created=False,
...)`` with the current row counts.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import normalize_issuer
from app.db.models import (
    Allocation,
    BomLine,
    BomVersion,
    Customer,
    CycleObservation,
    DefectObservation,
    ExpectedReceipt,
    Factory,
    Inspection,
    Line,
    LineCapability,
    LineCapacitySlot,
    LineMeasurement,
    Material,
    MaterialBalance,
    MaterialLot,
    Membership,
    OperationStaffing,
    OperatorAlias,
    Order,
    Organization,
    QualityHold,
    QualityPolicyVersion,
    QualityRelease,
    Reservation,
    RoleAssignment,
    SkillRecord,
    StockMovement,
    Style,
    StyleOperation,
    User,
)
from app.domain.inventory.calc import available_now, gross_demand, shortage
from app.domain.inventory.calc import material_state as derive_material_state
from app.domain.planning.calc import SlotCapacity, plan_earliest_slots
from app.domain.quality.calc import QualityPolicyRules, evaluate_inspection
from app.domain.vocab import (
    AllocationStatus,
    ExpectedReceiptStatus,
    InspectionType,
    MaterialLotStatus,
    MaterialState,
    MovementType,
    OrderSource,
    PolicyStatus,
    ProductionState,
    QualityHoldStatus,
    QualityState,
    ReservationStatus,
)
from app.seed import scenario as demo_scenario
from app.seed.identities import (
    DEMO_FACTORIES,
    DEMO_IDENTITIES,
    DEMO_ORG_NAME,
    DEMO_ORG_SLUG,
    DemoIdentity,
)
from app.seed.vocabulary import (
    BYG_LINES,
    BYG_ORDER_REFS,
    DEFECT_CATALOG,
    DEMO_ORDER_REF,
    KTN_LINES,
    KTN_ORDER_REFS,
    MATERIALS,
    OPERATION_CATALOG,
    SKILL_CODES,
    STYLE_CODES,
)

__all__ = [
    "DEMO_FACTORIES",
    "DEMO_IDENTITIES",
    "DEMO_ORDER_REF",
    "DEMO_ORG_NAME",
    "DEMO_ORG_SLUG",
    "DemoIdentity",
    "SeedSummary",
    "seed_demo",
]

_MONEY_2DP = Decimal("0.01")
_MONEY_4DP = Decimal("0.0001")

_PRODUCT_TYPES: tuple[str, ...] = (
    "Polo Shirt",
    "Crew T-Shirt",
    "Henley Shirt",
    "Tank Top",
    "Hoodie",
    "Shorts",
)

_MACHINE_TYPE_BY_SKILL: dict[str, str] = {
    "OL": "overlock",
    "SNLS": "single-needle",
    "FL": "flatlock",
    "BH": "buttonhole",
    "BT": "bartack",
    "PRESS": "press",
    "QC": "inspection table",
}

# Orders cycle through these production states round-robin so every state is
# represented in a roughly even split across the ~100 seeded orders.
_ORDER_STATE_CYCLE: tuple[ProductionState, ...] = (
    ProductionState.DRAFT,
    ProductionState.VALIDATED,
    ProductionState.PLANNED,
    ProductionState.IN_PRODUCTION,
    ProductionState.PRODUCTION_COMPLETE,
)

_STATES_NEEDING_CAPACITY = frozenset(
    {ProductionState.PLANNED, ProductionState.IN_PRODUCTION, ProductionState.PRODUCTION_COMPLETE}
)

# Orders are scheduled starting no earlier than `due_date - _ALLOCATION_LEAD_DAYS`
# (clamped to `anchor_date`), not always from `anchor_date`: a planner would
# not normally book capacity for a far-future order into the very first
# available days, and reserving the demo scenario's anchor..anchor+5 window
# for orders that are actually due that early keeps its "capacity comfortably
# exceeds the order's requirement" fact true regardless of how many of the
# ~100 random orders end up needing capacity.
_ALLOCATION_LEAD_DAYS = 14

# The 4 styles that additionally get a superseded (inactive) BOM version.
_STYLES_WITH_BOM_HISTORY = frozenset({"ST-01", "ST-02", "ST-04", "ST-05"})

# The 6 styles carrying the IE cycle-observation dataset (must include the
# demo style so its bottleneck numbers are backed by real observation rows).
_IE_STYLE_CODES: tuple[str, ...] = STYLE_CODES[:6]
_IE_LINE_CODES: tuple[str, ...] = ("L1", "L2", "L3")
_IE_SAMPLE_DAY_OFFSETS: tuple[int, ...] = (0, 6, 12, 18, 24)

_QUALITY_POLICY_CODE = "QP-DEMO"
_QUALITY_POLICY_RULES: dict[str, object] = {
    "sample_size": 80,
    "max_defective_units": 5,
    "max_critical_defects": 0,
    "required_inspection_types": ["FINAL"],
}

# Worse-outcome ranking used to combine several BOM lines' material states
# into one order-level state (`_finalize_material_states`).
_MATERIAL_STATE_SEVERITY: dict[MaterialState, int] = {
    MaterialState.READY: 0,
    MaterialState.AT_RISK: 1,
    MaterialState.SHORTAGE: 2,
    MaterialState.UNKNOWN: 3,
}


def _anchor_datetime(anchor_date: date) -> datetime:
    """08:00 Asia/Colombo on `anchor_date`, converted to UTC.

    The single time-of-day reference every non-date timestamp in the
    generator is computed relative to (plus a deterministic offset) —
    never `datetime.now()`.
    """
    local = datetime.combine(anchor_date, time(8, 0), tzinfo=ZoneInfo("Asia/Colombo"))
    return local.astimezone(UTC)


@dataclass(frozen=True)
class SeedSummary:
    """Result of a ``seed_demo`` call."""

    created: bool
    organization_id: uuid.UUID
    demo_order_id: uuid.UUID | None
    counts: dict[str, int] = field(default_factory=dict)


def _quantize_minutes(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_2DP, rounding=ROUND_FLOOR)


def _quantize_units(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_4DP, rounding=ROUND_FLOOR)


async def seed_demo(
    session: AsyncSession, *, anchor_date: date, issuer: str, rng_seed: int = 20260917
) -> SeedSummary:
    """Create (idempotently) the full synthetic demonstration dataset.

    Returns ``created=False`` and the current row counts without writing
    anything when the ``demo-apparel`` organization already exists.
    """
    existing = await session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    if existing is not None:
        demo_order = await session.scalar(
            select(Order).where(
                Order.organization_id == existing.id, Order.external_ref == DEMO_ORDER_REF
            )
        )
        counts = await _count_tables(session, existing.id)
        return SeedSummary(
            created=False,
            organization_id=existing.id,
            demo_order_id=demo_order.id if demo_order else None,
            counts=counts,
        )

    rng = random.Random(rng_seed)
    normalized_issuer = normalize_issuer(issuer)

    org, factories, users = await _seed_identities(session, issuer=normalized_issuer)
    customers = await _seed_customers(session, org)
    materials = await _seed_materials(session, org, rng)
    styles, style_ops = await _seed_styles(session, org, rng)
    bom_versions = await _seed_boms(session, rng, styles, materials, users, anchor_date)
    lines, line_capabilities = await _seed_lines(session, factories, rng)
    slots = await _seed_capacity_slots(session, lines, anchor_date, rng)

    orders, order_line = await _seed_orders(
        session,
        org=org,
        factories=factories,
        customers=customers,
        styles=styles,
        style_ops=style_ops,
        bom_versions=bom_versions,
        lines=lines,
        line_capabilities=line_capabilities,
        slots=slots,
        anchor_date=anchor_date,
        rng=rng,
    )

    await _seed_inventory(
        session,
        org=org,
        factories=factories,
        materials=materials,
        orders=orders,
        anchor_date=anchor_date,
        rng=rng,
    )

    await _seed_ie(
        session,
        org=org,
        factories=factories,
        lines=lines,
        styles=styles,
        style_ops=style_ops,
        anchor_date=anchor_date,
        rng=rng,
    )

    policy_version = await _seed_quality_policy(session, org, users, anchor_date)
    await _seed_quality_inspections(
        session,
        orders=orders,
        order_line=order_line,
        policy_version=policy_version,
        users=users,
        rng=rng,
        anchor_date=anchor_date,
    )

    demo_order = await _seed_demo_scenario(
        session,
        org=org,
        factories=factories,
        customers=customers,
        style=styles[demo_scenario.DEMO_STYLE_CODE],
        bom_version=bom_versions[demo_scenario.DEMO_STYLE_CODE],
        materials=materials,
        orders=orders,
        anchor_date=anchor_date,
    )

    # Recomputed last so every non-DRAFT order's `material_state` reflects
    # the fully-seeded balances/reservations/expected receipts (including
    # the demo scenario's), via the real domain function — never an
    # arbitrary random choice.
    await _finalize_material_states(session, orders=orders)

    await session.flush()
    counts = await _count_tables(session, org.id)
    return SeedSummary(
        created=True, organization_id=org.id, demo_order_id=demo_order.id, counts=counts
    )


# --- Identity ---------------------------------------------------------------


async def _seed_identities(
    session: AsyncSession, *, issuer: str
) -> tuple[Organization, dict[str, Factory], dict[str, User]]:
    org = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG)
    session.add(org)
    await session.flush()

    factories: dict[str, Factory] = {}
    for code, name in DEMO_FACTORIES:
        factory = Factory(organization_id=org.id, code=code, name=name)
        session.add(factory)
        factories[code] = factory
    await session.flush()

    users: dict[str, User] = {}
    for email, subject, display_name, role, factory_code in DEMO_IDENTITIES:
        user = User(issuer=issuer, subject=subject, email=email, display_name=display_name)
        session.add(user)
        await session.flush()
        users[email] = user

        membership = Membership(organization_id=org.id, user_id=user.id)
        session.add(membership)
        await session.flush()

        factory_id = factories[factory_code].id if factory_code is not None else None
        session.add(RoleAssignment(membership_id=membership.id, factory_id=factory_id, role=role))

    await session.flush()
    return org, factories, users


# --- Master data --------------------------------------------------------


async def _seed_customers(session: AsyncSession, org: Organization) -> dict[str, Customer]:
    customers: dict[str, Customer] = {}
    for i in range(1, 27):
        code = f"C{i:02d}"
        customer = Customer(organization_id=org.id, code=code, name=f"Customer {i:02d} Apparel Co.")
        session.add(customer)
        customers[code] = customer
    await session.flush()
    return customers


async def _seed_materials(
    session: AsyncSession, org: Organization, rng: random.Random
) -> dict[str, Material]:
    materials: dict[str, Material] = {}
    for code, name, unit in MATERIALS:
        pack_size = Decimal(rng.choice([100, 144, 200, 500])) if unit == "pcs" else None
        material = Material(
            organization_id=org.id,
            code=code,
            name=name,
            unit=unit,
            safety_stock=Decimal(rng.randint(50, 500)),
            lead_time_days=rng.randint(3, 21),
            pack_size=pack_size,
        )
        session.add(material)
        materials[code] = material
    await session.flush()
    return materials


async def _seed_styles(
    session: AsyncSession, org: Organization, rng: random.Random
) -> tuple[dict[str, Style], dict[str, list[StyleOperation]]]:
    styles: dict[str, Style] = {}
    style_ops: dict[str, list[StyleOperation]] = {}

    for i, code in enumerate(STYLE_CODES, start=1):
        style = Style(
            organization_id=org.id,
            code=code,
            name=f"Style {code} {_PRODUCT_TYPES[i % len(_PRODUCT_TYPES)]}",
            product_type=_PRODUCT_TYPES[i % len(_PRODUCT_TYPES)],
        )
        session.add(style)
        await session.flush()
        styles[code] = style

        ops: list[StyleOperation] = []
        if code == demo_scenario.DEMO_STYLE_CODE:
            for (
                sequence,
                op_code,
                name,
                sam_minutes,
                skill_code,
                machine_type,
            ) in demo_scenario.DEMO_STYLE_OPERATIONS:
                op = StyleOperation(
                    style_id=style.id,
                    sequence=sequence,
                    code=op_code,
                    name=name,
                    sam_minutes=sam_minutes,
                    skill_code=skill_code,
                    machine_type=machine_type,
                )
                session.add(op)
                ops.append(op)
        else:
            count = rng.randint(6, 10)
            indices = sorted(rng.sample(range(len(OPERATION_CATALOG)), count))
            for sequence, idx in enumerate(indices, start=1):
                name, skill_code = OPERATION_CATALOG[idx]
                sam_minutes = Decimal(str(round(rng.uniform(0.30, 2.50), 2)))
                op = StyleOperation(
                    style_id=style.id,
                    sequence=sequence,
                    code=f"OP-{sequence:02d}",
                    name=name,
                    sam_minutes=sam_minutes,
                    skill_code=skill_code,
                    machine_type=_MACHINE_TYPE_BY_SKILL[skill_code],
                )
                session.add(op)
                ops.append(op)
        await session.flush()
        style_ops[code] = ops

    return styles, style_ops


async def _seed_boms(
    session: AsyncSession,
    rng: random.Random,
    styles: dict[str, Style],
    materials: dict[str, Material],
    users: dict[str, User],
    anchor_date: date,
) -> dict[str, BomVersion]:
    active_versions: dict[str, BomVersion] = {}
    material_codes = list(materials.keys())
    approver = users["supervisor@demo.test"].id
    # BOMs are approved well before the anchor date (a business fact, not a
    # seeding-time fact) — derived from anchor_date, never `datetime.now()`.
    approved_at = _anchor_datetime(anchor_date) - timedelta(days=60)

    for code, style in styles.items():
        version_no = 1
        if code in _STYLES_WITH_BOM_HISTORY:
            old_version = BomVersion(
                style_id=style.id,
                version_no=1,
                is_active=False,
                approved_by=approver,
                approved_at=approved_at,
            )
            session.add(old_version)
            await session.flush()
            for material_code in rng.sample(material_codes, 3):
                material = materials[material_code]
                session.add(
                    BomLine(
                        bom_version_id=old_version.id,
                        material_id=material.id,
                        quantity_per_unit=Decimal(str(round(rng.uniform(0.1, 2.0), 4))),
                        unit=material.unit,
                        wastage_fraction=Decimal(str(round(rng.uniform(0.0, 0.15), 4))),
                    )
                )
            version_no = 2

        active_version = BomVersion(
            style_id=style.id,
            version_no=version_no,
            is_active=True,
            approved_by=approver,
            approved_at=approved_at,
        )
        session.add(active_version)
        await session.flush()

        if code == demo_scenario.DEMO_STYLE_CODE:
            demo_material = materials[demo_scenario.DEMO_BOM_MATERIAL_CODE]
            session.add(
                BomLine(
                    bom_version_id=active_version.id,
                    material_id=demo_material.id,
                    quantity_per_unit=demo_scenario.DEMO_BOM_QUANTITY_PER_UNIT,
                    unit=demo_material.unit,
                    wastage_fraction=demo_scenario.DEMO_BOM_WASTAGE_FRACTION,
                )
            )
            extra_codes = [c for c in rng.sample(material_codes, 3) if c != demo_material.code][:2]
            for material_code in extra_codes:
                material = materials[material_code]
                session.add(
                    BomLine(
                        bom_version_id=active_version.id,
                        material_id=material.id,
                        quantity_per_unit=Decimal(str(round(rng.uniform(0.1, 2.0), 4))),
                        unit=material.unit,
                        wastage_fraction=Decimal(str(round(rng.uniform(0.0, 0.15), 4))),
                    )
                )
        else:
            line_count = rng.randint(3, 5)
            for material_code in rng.sample(material_codes, line_count):
                material = materials[material_code]
                session.add(
                    BomLine(
                        bom_version_id=active_version.id,
                        material_id=material.id,
                        quantity_per_unit=Decimal(str(round(rng.uniform(0.1, 2.0), 4))),
                        unit=material.unit,
                        wastage_fraction=Decimal(str(round(rng.uniform(0.0, 0.15), 4))),
                    )
                )

        active_versions[code] = active_version

    await session.flush()
    return active_versions


async def _seed_lines(
    session: AsyncSession, factories: dict[str, Factory], rng: random.Random
) -> tuple[dict[str, Line], dict[str, set[str]]]:
    all_skills = set(SKILL_CODES)
    lines: dict[str, Line] = {}
    capabilities: dict[str, set[str]] = {}

    for factory_code, line_defs in (("KTN", KTN_LINES), ("BYG", BYG_LINES)):
        factory = factories[factory_code]
        for code, name in line_defs:
            line = Line(
                organization_id=factory.organization_id,
                factory_id=factory.id,
                code=code,
                name=name,
                operator_count=rng.randint(18, 30),
                is_active=True,
            )
            session.add(line)
            await session.flush()
            lines[code] = line

            skills = set(all_skills) - {"BH"} if code == "L6" else set(all_skills)
            capabilities[code] = skills
            for skill in sorted(skills):
                session.add(LineCapability(line_id=line.id, skill_code=skill))

    await session.flush()
    return lines, capabilities


async def _seed_capacity_slots(
    session: AsyncSession, lines: dict[str, Line], anchor_date: date, rng: random.Random
) -> dict[tuple[str, date, str], LineCapacitySlot]:
    slots: dict[tuple[str, date, str], LineCapacitySlot] = {}
    for code, line in lines.items():
        for offset in range(30):
            slot_date = anchor_date + timedelta(days=offset)
            for shift_code in ("A", "B"):
                slot = LineCapacitySlot(
                    organization_id=line.organization_id,
                    factory_id=line.factory_id,
                    line_id=line.id,
                    slot_date=slot_date,
                    shift_code=shift_code,
                    available_operator_minutes=Decimal(line.operator_count * 420),
                    planned_efficiency=Decimal(str(round(rng.uniform(0.70, 0.80), 4))),
                    allocated_standard_minutes=Decimal("0"),
                )
                session.add(slot)
                slots[(code, slot_date, shift_code)] = slot
    await session.flush()
    return slots


# --- Orders and capacity allocation -----------------------------------------


def _style_sam_total(style_ops: list[StyleOperation]) -> Decimal:
    return sum((op.sam_minutes for op in style_ops), Decimal("0"))


def _compatible_line_codes(
    required_skills: set[str], line_capabilities: dict[str, set[str]], candidate_codes: list[str]
) -> list[str]:
    return [code for code in candidate_codes if required_skills <= line_capabilities[code]]


async def _seed_orders(
    session: AsyncSession,
    *,
    org: Organization,
    factories: dict[str, Factory],
    customers: dict[str, Customer],
    styles: dict[str, Style],
    style_ops: dict[str, list[StyleOperation]],
    bom_versions: dict[str, BomVersion],
    lines: dict[str, Line],
    line_capabilities: dict[str, set[str]],
    slots: dict[tuple[str, date, str], LineCapacitySlot],
    anchor_date: date,
    rng: random.Random,
) -> tuple[dict[str, Order], dict[uuid.UUID, uuid.UUID]]:
    customer_codes = list(customers.keys())
    style_codes = list(styles.keys())
    order_line: dict[uuid.UUID, uuid.UUID] = {}
    orders: dict[str, Order] = {}

    ktn_line_codes = [code for code, _ in KTN_LINES]
    byg_line_codes = [code for code, _ in BYG_LINES]

    all_refs = [(ref, "KTN") for ref in KTN_ORDER_REFS] + [(ref, "BYG") for ref in BYG_ORDER_REFS]

    for index, (ref, factory_code) in enumerate(all_refs):
        factory = factories[factory_code]
        customer = customers[rng.choice(customer_codes)]
        style_code = rng.choice(style_codes)
        style = styles[style_code]
        ops = style_ops[style_code]
        quantity = rng.randint(300, 3000)
        due_date = anchor_date + timedelta(days=rng.randint(3, 45))
        priority = rng.randint(1, 5)
        intended_state = _ORDER_STATE_CYCLE[index % len(_ORDER_STATE_CYCLE)]

        # `material_state` is a placeholder here: DRAFT orders keep UNKNOWN
        # (no planning has happened yet); every other order's real state is
        # computed by `_finalize_material_states` once balances/reservations
        # exist, from actual BOM demand — never a random choice.
        order = Order(
            organization_id=org.id,
            factory_id=factory.id,
            customer_id=customer.id,
            style_id=style.id,
            bom_version_id=bom_versions[style_code].id,
            external_ref=ref,
            quantity=quantity,
            produced_units=0,
            packed_units=0,
            due_date=due_date,
            priority=priority,
            production_state=intended_state.value,
            material_state=MaterialState.UNKNOWN.value,
            quality_state=QualityState.NOT_INSPECTED.value,
            source=OrderSource.SYNTHETIC_SEED.value,
        )
        session.add(order)
        await session.flush()
        orders[ref] = order

        final_state = intended_state
        if intended_state in _STATES_NEEDING_CAPACITY:
            required_skills = {op.skill_code for op in ops}
            candidate_codes = ktn_line_codes if factory_code == "KTN" else byg_line_codes
            compatible = _compatible_line_codes(required_skills, line_capabilities, candidate_codes)
            earliest_date = max(anchor_date, due_date - timedelta(days=_ALLOCATION_LEAD_DAYS))
            primary_line_id = (
                _allocate_order(
                    session,
                    order=order,
                    compatible_codes=compatible,
                    lines=lines,
                    sam_total=_style_sam_total(ops),
                    slots=slots,
                    earliest_date=earliest_date,
                    due_date=due_date,
                )
                if compatible
                else None
            )
            if primary_line_id is not None:
                order_line[order.id] = primary_line_id
            else:
                # No compatible line had capacity left in the window: never
                # leave a PLANNED/IN_PRODUCTION/PRODUCTION_COMPLETE order
                # without at least one ACTIVE allocation.
                final_state = ProductionState.VALIDATED

        if final_state == ProductionState.IN_PRODUCTION:
            order.produced_units = rng.randint(1, max(1, quantity - 1))
            order.packed_units = rng.randint(0, order.produced_units)
        elif final_state == ProductionState.PRODUCTION_COMPLETE:
            order.produced_units = quantity
            order.packed_units = quantity
        order.production_state = final_state.value

    await session.flush()
    return orders, order_line


def _allocate_order(
    session: AsyncSession,
    *,
    order: Order,
    compatible_codes: list[str],
    lines: dict[str, Line],
    sam_total: Decimal,
    slots: dict[tuple[str, date, str], LineCapacitySlot],
    earliest_date: date,
    due_date: date,
) -> uuid.UUID | None:
    """Allocate `order` across every compatible line's capacity slots in
    ``[earliest_date, due_date]``, filling the earliest available capacity
    first regardless of which compatible line it falls on (never restricted
    to a single rng-chosen line, so a saturated line can never leave an
    order with zero allocations while a sibling compatible line still has
    room). Returns the first allocation's line id, or ``None`` if no
    capacity was available on any compatible line.
    """
    if sam_total <= 0 or not compatible_codes:
        return None

    slot_lookup: dict[uuid.UUID, LineCapacitySlot] = {}
    slot_capacities: list[SlotCapacity] = []
    for (code, slot_date, shift_code), slot in slots.items():
        if code not in compatible_codes or not (earliest_date <= slot_date <= due_date):
            continue
        slot_lookup[slot.id] = slot
        slot_capacities.append(
            SlotCapacity(
                slot_id=slot.id,
                line_id=lines[code].id,
                slot_date=slot_date,
                shift_code=shift_code,
                available_operator_minutes=slot.available_operator_minutes,
                planned_efficiency=slot.planned_efficiency,
                allocated_standard_minutes=slot.allocated_standard_minutes,
            )
        )
    if not slot_capacities:
        return None

    compatible_line_ids = frozenset(lines[code].id for code in compatible_codes)
    plan = plan_earliest_slots(
        units=order.quantity,
        sam_minutes_per_unit=sam_total,
        slots=slot_capacities,
        earliest_date=earliest_date,
        due_date=due_date,
        compatible_line_ids=compatible_line_ids,
    )

    primary_line_id: uuid.UUID | None = None
    for allocation in plan.allocations:
        standard_minutes = _quantize_minutes(allocation.standard_minutes)
        units = _quantize_units(allocation.units)
        if standard_minutes <= 0 or units <= 0:
            continue
        session.add(
            Allocation(
                organization_id=order.organization_id,
                factory_id=order.factory_id,
                order_id=order.id,
                slot_id=allocation.slot_id,
                standard_minutes=standard_minutes,
                units=units,
                status=AllocationStatus.ACTIVE.value,
            )
        )
        slot = slot_lookup[allocation.slot_id]
        slot.allocated_standard_minutes = slot.allocated_standard_minutes + standard_minutes
        if primary_line_id is None:
            primary_line_id = allocation.line_id
    return primary_line_id


# --- Inventory ---------------------------------------------------------------


def _is_demo_material_at_ktn(factory_code: str, material_code: str) -> bool:
    """True only for KTN's M01 — its ledger/balance are the demo scenario's
    exact deterministic numbers (`_seed_demo_material_ledger`), never the
    generic random path. BYG's M01 is an ordinary material.
    """
    return factory_code == "KTN" and material_code == demo_scenario.DEMO_BOM_MATERIAL_CODE


async def _seed_demo_material_ledger(
    session: AsyncSession,
    *,
    org: Organization,
    factory: Factory,
    material: Material,
    anchor_date: date,
) -> None:
    """KTN M01's deterministic ledger and balance (Task 6 brief section 4):
    one receipt lot, then a fixed 14-day daily issue of exactly
    `DEMO_ISSUE_DAILY_QUANTITY`, so `average_daily_consumption` is exactly
    the target and `on_hand_accepted` is exactly the demo balance by
    construction. `reserved` starts at 0 here; `_seed_demo_scenario` sets
    it to the demo's exact reservation amount once the "other order" it
    reserves against exists.
    """
    receipt_at = anchor_date - timedelta(days=demo_scenario.DEMO_ISSUE_WINDOW_DAYS + 1)
    lot = MaterialLot(
        organization_id=org.id,
        factory_id=factory.id,
        material_id=material.id,
        lot_code=f"LOT-{material.code}-DEMO",
        status=MaterialLotStatus.ACCEPTED.value,
        received_at=datetime.combine(receipt_at, datetime.min.time(), tzinfo=UTC),
    )
    session.add(lot)
    await session.flush()

    session.add(
        StockMovement(
            organization_id=org.id,
            factory_id=factory.id,
            material_id=material.id,
            lot_id=lot.id,
            movement_type=MovementType.RECEIPT.value,
            quantity=demo_scenario.DEMO_RECEIPT_LOT_QUANTITY,
            created_at=datetime.combine(receipt_at, datetime.min.time(), tzinfo=UTC),
        )
    )
    for offset in range(demo_scenario.DEMO_ISSUE_WINDOW_DAYS, 0, -1):
        issue_date = anchor_date - timedelta(days=offset)
        session.add(
            StockMovement(
                organization_id=org.id,
                factory_id=factory.id,
                material_id=material.id,
                lot_id=lot.id,
                movement_type=MovementType.ISSUE.value,
                quantity=-demo_scenario.DEMO_ISSUE_DAILY_QUANTITY,
                created_at=datetime.combine(issue_date, datetime.min.time(), tzinfo=UTC),
            )
        )

    session.add(
        MaterialBalance(
            organization_id=org.id,
            factory_id=factory.id,
            material_id=material.id,
            on_hand_accepted=demo_scenario.DEMO_BALANCE_ON_HAND_ACCEPTED,
            reserved=Decimal("0"),
        )
    )


async def _seed_inventory(
    session: AsyncSession,
    *,
    org: Organization,
    factories: dict[str, Factory],
    materials: dict[str, Material],
    orders: dict[str, Order],
    anchor_date: date,
    rng: random.Random,
) -> None:
    """Lots/movements/balances/reservations/expected receipts for **both**
    factories. KTN's M01 uses the demo scenario's exact deterministic
    ledger (`_seed_demo_material_ledger`); every other (factory, material)
    pair gets a generous random receipt and a random 14-day issue history.
    """
    for factory_code, factory in factories.items():
        for code, material in materials.items():
            if _is_demo_material_at_ktn(factory_code, code):
                await _seed_demo_material_ledger(
                    session, org=org, factory=factory, material=material, anchor_date=anchor_date
                )
                continue

            lot_quantity = Decimal(rng.randint(50_000, 150_000))
            lot = MaterialLot(
                organization_id=org.id,
                factory_id=factory.id,
                material_id=material.id,
                lot_code=f"LOT-{factory_code}-{code}-0001",
                status=MaterialLotStatus.ACCEPTED.value,
                received_at=datetime.combine(
                    anchor_date - timedelta(days=30), datetime.min.time(), tzinfo=UTC
                ),
            )
            session.add(lot)
            await session.flush()

            session.add(
                StockMovement(
                    organization_id=org.id,
                    factory_id=factory.id,
                    material_id=material.id,
                    lot_id=lot.id,
                    movement_type=MovementType.RECEIPT.value,
                    quantity=lot_quantity,
                    created_at=datetime.combine(
                        anchor_date - timedelta(days=30), datetime.min.time(), tzinfo=UTC
                    ),
                )
            )

            total_issued = Decimal("0")
            for offset in range(14, 0, -1):
                issue_qty = Decimal(str(round(rng.uniform(0, float(lot_quantity) * 0.01), 2)))
                if issue_qty <= 0:
                    continue
                issue_date = anchor_date - timedelta(days=offset)
                session.add(
                    StockMovement(
                        organization_id=org.id,
                        factory_id=factory.id,
                        material_id=material.id,
                        lot_id=lot.id,
                        movement_type=MovementType.ISSUE.value,
                        quantity=-issue_qty,
                        created_at=datetime.combine(issue_date, datetime.min.time(), tzinfo=UTC),
                    )
                )
                total_issued += issue_qty

            on_hand = lot_quantity - total_issued

            session.add(
                MaterialBalance(
                    organization_id=org.id,
                    factory_id=factory.id,
                    material_id=material.id,
                    on_hand_accepted=on_hand,
                    reserved=Decimal("0"),
                )
            )

    await session.flush()

    # Reservations: gross BOM demand of every PLANNED order, per (factory,
    # material) — both KTN and BYG. M01 is always skipped: its only
    # reservation is the demo scenario's deliberate, exact one.
    bom_lines_by_version: dict[uuid.UUID, list[BomLine]] = {}
    reserved_by_key: dict[tuple[str, str], Decimal] = {}
    planned_orders = [
        o for o in orders.values() if o.production_state == ProductionState.PLANNED.value
    ]
    for order in planned_orders:
        factory_code = next(code for code, f in factories.items() if f.id == order.factory_id)
        bom_version_id = order.bom_version_id
        if bom_version_id not in bom_lines_by_version:
            result = await session.execute(
                select(BomLine).where(BomLine.bom_version_id == bom_version_id)
            )
            bom_lines_by_version[bom_version_id] = list(result.scalars())
        for bom_line in bom_lines_by_version[bom_version_id]:
            material_code = next(
                (code for code, mat in materials.items() if mat.id == bom_line.material_id), None
            )
            if material_code is None or material_code == demo_scenario.DEMO_BOM_MATERIAL_CODE:
                continue
            demand = gross_demand(
                Decimal(order.quantity), bom_line.quantity_per_unit, bom_line.wastage_fraction
            )
            demand = _quantize_units(demand)
            if demand <= 0:
                continue
            session.add(
                Reservation(
                    organization_id=org.id,
                    factory_id=order.factory_id,
                    material_id=bom_line.material_id,
                    order_id=order.id,
                    quantity=demand,
                    status=ReservationStatus.ACTIVE.value,
                )
            )
            key = (factory_code, material_code)
            reserved_by_key[key] = reserved_by_key.get(key, Decimal("0")) + demand

    if reserved_by_key:
        balance_result = await session.execute(select(MaterialBalance))
        balances_by_key: dict[tuple[uuid.UUID, uuid.UUID], MaterialBalance] = {
            (b.factory_id, b.material_id): b for b in balance_result.scalars()
        }
        for (factory_code, material_code), reserved_total in reserved_by_key.items():
            balance = balances_by_key.get((factories[factory_code].id, materials[material_code].id))
            if balance is None:
                continue
            # The receipt buffer (50k-150k) is sized generously above any
            # plausible BOM demand from the PLANNED orders sharing a
            # material so this never needs clamping; asserting rather than
            # clamping keeps the `reserved == sum(active reservations)`
            # invariant exact.
            assert reserved_total <= balance.on_hand_accepted, (
                f"{factory_code} material {material_code} reserved {reserved_total} "
                f"exceeds on_hand {balance.on_hand_accepted}"
            )
            balance.reserved = reserved_total

    # A handful of open expected receipts per factory, for non-demo materials.
    for factory_code, factory in factories.items():
        other_codes = [
            code for code in materials if not _is_demo_material_at_ktn(factory_code, code)
        ]
        for material_code in rng.sample(other_codes, 5):
            material = materials[material_code]
            session.add(
                ExpectedReceipt(
                    organization_id=org.id,
                    factory_id=factory.id,
                    material_id=material.id,
                    quantity=Decimal(rng.randint(500, 5000)),
                    expected_date=anchor_date + timedelta(days=rng.randint(1, 20)),
                    supplier_ref=f"SUP-{factory_code}-{material_code}-{rng.randint(1000, 9999)}",
                    status=ExpectedReceiptStatus.OPEN.value,
                )
            )

    await session.flush()


async def _finalize_material_states(session: AsyncSession, *, orders: dict[str, Order]) -> None:
    """Recompute every non-DRAFT order's `material_state` from its actual
    BOM demand against the fully-seeded balances/expected receipts, via
    `app.domain.inventory.calc` — never an arbitrary random choice. DRAFT
    orders keep the `UNKNOWN` placeholder (no planning has happened yet).
    Run last, after the demo scenario, so it sees the demo's own
    reservation/expected-receipt too.
    """
    balances = list((await session.execute(select(MaterialBalance))).scalars())
    balance_by_key = {(b.factory_id, b.material_id): b for b in balances}

    open_receipts = list(
        (
            await session.execute(
                select(ExpectedReceipt).where(
                    ExpectedReceipt.status == ExpectedReceiptStatus.OPEN.value
                )
            )
        ).scalars()
    )
    receipts_by_key: dict[tuple[uuid.UUID, uuid.UUID], list[ExpectedReceipt]] = {}
    for receipt in open_receipts:
        receipts_by_key.setdefault((receipt.factory_id, receipt.material_id), []).append(receipt)

    bom_lines_by_version: dict[uuid.UUID, list[BomLine]] = {}

    for order in orders.values():
        if order.production_state == ProductionState.DRAFT.value:
            continue
        if order.bom_version_id not in bom_lines_by_version:
            result = await session.execute(
                select(BomLine).where(BomLine.bom_version_id == order.bom_version_id)
            )
            bom_lines_by_version[order.bom_version_id] = list(result.scalars())
        lines = bom_lines_by_version[order.bom_version_id]
        if not lines:
            continue

        worst = MaterialState.READY
        for line in lines:
            balance = balance_by_key.get((order.factory_id, line.material_id))
            if balance is None:
                worst = MaterialState.UNKNOWN
                continue
            available = available_now(balance.on_hand_accepted, balance.reserved)
            demand = gross_demand(
                Decimal(order.quantity), line.quantity_per_unit, line.wastage_fraction
            )
            current_shortage = shortage(available, demand)
            eligible_receipts = sum(
                (
                    receipt.quantity
                    for receipt in receipts_by_key.get((order.factory_id, line.material_id), [])
                    if receipt.expected_date <= order.due_date
                ),
                Decimal("0"),
            )
            projected_shortage = shortage(available + eligible_receipts, demand)
            line_state = derive_material_state(current_shortage, projected_shortage, True)
            if _MATERIAL_STATE_SEVERITY[line_state] > _MATERIAL_STATE_SEVERITY[worst]:
                worst = line_state

        order.material_state = worst.value

    await session.flush()


# --- Industrial engineering ---------------------------------------------


async def _seed_ie(
    session: AsyncSession,
    *,
    org: Organization,
    factories: dict[str, Factory],
    lines: dict[str, Line],
    styles: dict[str, Style],
    style_ops: dict[str, list[StyleOperation]],
    anchor_date: date,
    rng: random.Random,
) -> list[OperatorAlias]:
    factory = factories["KTN"]
    ktn_line_codes = [code for code, _ in KTN_LINES]

    aliases: list[OperatorAlias] = []
    aliases_by_line: dict[str, list[OperatorAlias]] = {code: [] for code in ktn_line_codes}
    counter = 1
    for code in ktn_line_codes:
        line = lines[code]
        for _ in range(25):
            alias = OperatorAlias(
                organization_id=org.id,
                factory_id=factory.id,
                alias_code=f"KTN-OP-{counter:03d}",
                line_id=line.id,
                is_active=True,
            )
            session.add(alias)
            aliases.append(alias)
            aliases_by_line[code].append(alias)
            counter += 1
    await session.flush()

    skills_without_bh = sorted(set(SKILL_CODES) - {"BH"})
    for code, line_aliases in aliases_by_line.items():
        skills_for_line = sorted(SKILL_CODES) if code != "L6" else skills_without_bh
        for alias in line_aliases:
            for skill in rng.sample(skills_for_line, rng.randint(1, 3)):
                session.add(
                    SkillRecord(
                        operator_alias_id=alias.id, skill_code=skill, level=rng.randint(1, 4)
                    )
                )
    await session.flush()

    outliers_remaining = 2
    for style_code in _IE_STYLE_CODES:
        style = styles[style_code]
        ops = style_ops[style_code]
        for line_code in _IE_LINE_CODES:
            line = lines[line_code]
            for op in ops:
                if (
                    style_code == demo_scenario.DEMO_STYLE_CODE
                    and line_code == demo_scenario.DEMO_IE_LINE_CODE
                ):
                    parallel = demo_scenario.DEMO_IE_PARALLEL_OPERATORS_BY_OPERATION[op.code]
                else:
                    parallel = rng.randint(1, 3)
                session.add(
                    OperationStaffing(
                        organization_id=org.id,
                        factory_id=factory.id,
                        line_id=line.id,
                        style_id=style.id,
                        operation_id=op.id,
                        parallel_operators=parallel,
                    )
                )

                if (
                    style_code == demo_scenario.DEMO_STYLE_CODE
                    and line_code == demo_scenario.DEMO_IE_LINE_CODE
                ):
                    samples = demo_scenario.DEMO_IE_OBSERVED_SECONDS_BY_OPERATION[op.code]
                else:
                    nominal = float(op.sam_minutes) * 60
                    samples = tuple(
                        Decimal(str(round(nominal * rng.uniform(0.85, 1.15), 2)))
                        for _ in _IE_SAMPLE_DAY_OFFSETS
                    )

                for sample_index, (offset, seconds) in enumerate(
                    zip(_IE_SAMPLE_DAY_OFFSETS, samples, strict=True)
                ):
                    observed_seconds = seconds
                    is_outlier = False
                    if (
                        outliers_remaining > 0
                        and style_code == _IE_STYLE_CODES[0]
                        and line_code == _IE_LINE_CODES[0]
                        and sample_index == 0
                    ):
                        observed_seconds = seconds * Decimal("2.5")
                        is_outlier = True
                        outliers_remaining -= 1
                    alias = aliases_by_line[line_code][
                        rng.randrange(len(aliases_by_line[line_code]))
                    ]
                    session.add(
                        CycleObservation(
                            organization_id=org.id,
                            factory_id=factory.id,
                            line_id=line.id,
                            style_id=style.id,
                            operation_id=op.id,
                            operator_alias_id=alias.id,
                            observed_seconds=observed_seconds.quantize(Decimal("0.01")),
                            observed_at=datetime.combine(
                                anchor_date - timedelta(days=29 - offset),
                                datetime.min.time(),
                                tzinfo=UTC,
                            ),
                            is_outlier=is_outlier,
                        )
                    )

            for day_offset in range(0, 30, 5):
                session.add(
                    LineMeasurement(
                        organization_id=org.id,
                        factory_id=factory.id,
                        line_id=line.id,
                        style_id=style.id,
                        measured_on=anchor_date - timedelta(days=29 - day_offset),
                        hours=Decimal("8.00"),
                        units_output=rng.randint(50, 400),
                    )
                )

    await session.flush()
    return aliases


# --- Quality -------------------------------------------------------------


async def _seed_quality_policy(
    session: AsyncSession, org: Organization, users: dict[str, User], anchor_date: date
) -> QualityPolicyVersion:
    policy = QualityPolicyVersion(
        organization_id=org.id,
        code=_QUALITY_POLICY_CODE,
        version_no=1,
        is_demo=True,
        status=PolicyStatus.ACTIVE.value,
        rules=_QUALITY_POLICY_RULES,
        approved_by=users["quality@demo.test"].id,
        # Approved well before the anchor date — a business fact, never
        # `datetime.now()`.
        approved_at=_anchor_datetime(anchor_date) - timedelta(days=90),
    )
    session.add(policy)
    await session.flush()
    return policy


async def _seed_quality_inspections(
    session: AsyncSession,
    *,
    orders: dict[str, Order],
    order_line: dict[uuid.UUID, uuid.UUID],
    policy_version: QualityPolicyVersion,
    users: dict[str, User],
    rng: random.Random,
    anchor_date: date,
) -> None:
    rules = QualityPolicyRules.from_json(policy_version.rules)
    quality_user = users["quality@demo.test"].id
    anchor_dt = _anchor_datetime(anchor_date)

    failed_hold_assigned = False
    no_release_assigned = False

    for order in orders.values():
        state = ProductionState(order.production_state)
        line_id = order_line.get(order.id)
        if state == ProductionState.IN_PRODUCTION:
            inspected_units = min(order.produced_units, rng.randint(20, 80)) or 1
            defective_units = rng.randint(0, 2)
            evaluation = evaluate_inspection(
                rules,
                inspected_units=inspected_units,
                defective_units=defective_units,
                critical_defects=0,
            )
            inspection = Inspection(
                organization_id=order.organization_id,
                factory_id=order.factory_id,
                order_id=order.id,
                line_id=line_id,
                inspection_type=InspectionType.INLINE.value,
                inspected_units=inspected_units,
                defective_units=defective_units,
                policy_version_id=policy_version.id,
                result=evaluation.result,
                inspected_by=quality_user,
                # Recent, but deterministic (rng-derived, anchor-relative)
                # rather than `datetime.now()`.
                inspected_at=anchor_dt - timedelta(days=rng.randint(0, 5), hours=rng.randint(0, 8)),
            )
            session.add(inspection)
            await session.flush()
            if defective_units:
                session.add(
                    DefectObservation(
                        inspection_id=inspection.id,
                        defect_code=DEFECT_CATALOG[0][0],
                        severity=DEFECT_CATALOG[0][2],
                        count=defective_units,
                    )
                )
            order.quality_state = QualityState.PENDING.value

        elif state == ProductionState.PRODUCTION_COMPLETE:
            inspected_units = 100
            if not failed_hold_assigned:
                defective_units = 10
                failed_hold_assigned = True
            else:
                defective_units = rng.randint(0, 2)

            evaluation = evaluate_inspection(
                rules,
                inspected_units=inspected_units,
                defective_units=defective_units,
                critical_defects=0,
            )
            inspection = Inspection(
                organization_id=order.organization_id,
                factory_id=order.factory_id,
                order_id=order.id,
                line_id=line_id,
                inspection_type=InspectionType.FINAL.value,
                inspected_units=inspected_units,
                defective_units=defective_units,
                policy_version_id=policy_version.id,
                result=evaluation.result,
                inspected_by=quality_user,
                inspected_at=anchor_dt - timedelta(days=rng.randint(0, 3), hours=rng.randint(0, 8)),
            )
            session.add(inspection)
            await session.flush()
            if defective_units:
                defect_code, _, severity = DEFECT_CATALOG[1]
                session.add(
                    DefectObservation(
                        inspection_id=inspection.id,
                        defect_code=defect_code,
                        severity=severity,
                        count=defective_units,
                    )
                )

            if evaluation.result == "FAIL":
                session.add(
                    QualityHold(
                        organization_id=order.organization_id,
                        factory_id=order.factory_id,
                        order_id=order.id,
                        inspection_id=inspection.id,
                        reason="Failed final inspection",
                        status=QualityHoldStatus.ACTIVE.value,
                        created_by=None,
                    )
                )
                order.quality_state = QualityState.HOLD.value
            elif not no_release_assigned:
                no_release_assigned = True
                order.quality_state = QualityState.PENDING.value
            else:
                session.add(
                    QualityRelease(
                        organization_id=order.organization_id,
                        factory_id=order.factory_id,
                        order_id=order.id,
                        inspection_id=inspection.id,
                        policy_version_id=policy_version.id,
                        released_by=quality_user,
                        # A few hours after the inspection it releases —
                        # never `datetime.now()`.
                        released_at=inspection.inspected_at + timedelta(hours=4),
                    )
                )
                order.quality_state = QualityState.RELEASED.value

    await session.flush()


# --- Demo scenario -------------------------------------------------------


async def _seed_demo_scenario(
    session: AsyncSession,
    *,
    org: Organization,
    factories: dict[str, Factory],
    customers: dict[str, Customer],
    style: Style,
    bom_version: BomVersion,
    materials: dict[str, Material],
    orders: dict[str, Order],
    anchor_date: date,
) -> Order:
    factory = factories["KTN"]
    customer = customers[demo_scenario.DEMO_CUSTOMER_CODE]
    material = materials[demo_scenario.DEMO_BOM_MATERIAL_CODE]

    demo_order = Order(
        organization_id=org.id,
        factory_id=factory.id,
        customer_id=customer.id,
        style_id=style.id,
        bom_version_id=bom_version.id,
        external_ref=DEMO_ORDER_REF,
        quantity=demo_scenario.DEMO_ORDER_QUANTITY,
        produced_units=0,
        packed_units=0,
        due_date=anchor_date + timedelta(days=demo_scenario.DEMO_ORDER_DUE_OFFSET_DAYS),
        priority=demo_scenario.DEMO_ORDER_PRIORITY,
        production_state=ProductionState.VALIDATED.value,
        material_state=MaterialState.UNKNOWN.value,
        quality_state=QualityState.NOT_INSPECTED.value,
        source=OrderSource.SYNTHETIC_SEED.value,
    )
    session.add(demo_order)
    await session.flush()

    # KTN M01's lot/ledger/balance were already created deterministically by
    # `_seed_demo_material_ledger` (called from `_seed_inventory`, which
    # runs before this scenario step so `_finalize_material_states` always
    # sees a real balance for M01). Only the demo's own reservation is
    # applied here, against the balance that already exists.
    balance = await session.scalar(
        select(MaterialBalance).where(
            MaterialBalance.factory_id == factory.id, MaterialBalance.material_id == material.id
        )
    )
    assert balance is not None, "KTN M01 balance must exist before the demo scenario runs"
    assert balance.on_hand_accepted == demo_scenario.DEMO_BALANCE_ON_HAND_ACCEPTED
    balance.reserved = demo_scenario.DEMO_BALANCE_RESERVED

    other_order = next(
        (
            o
            for o in orders.values()
            if o.factory_id == factory.id
            and o.external_ref != DEMO_ORDER_REF
            and o.production_state == ProductionState.PLANNED.value
        ),
        next(
            o
            for o in orders.values()
            if o.factory_id == factory.id and o.external_ref != DEMO_ORDER_REF
        ),
    )
    session.add(
        Reservation(
            organization_id=org.id,
            factory_id=factory.id,
            material_id=material.id,
            order_id=other_order.id,
            quantity=demo_scenario.DEMO_OTHER_RESERVATION_QUANTITY,
            status=ReservationStatus.ACTIVE.value,
        )
    )

    session.add(
        ExpectedReceipt(
            organization_id=org.id,
            factory_id=factory.id,
            material_id=material.id,
            quantity=demo_scenario.DEMO_EXPECTED_RECEIPT_QUANTITY,
            expected_date=anchor_date
            + timedelta(days=demo_scenario.DEMO_EXPECTED_RECEIPT_OFFSET_DAYS),
            supplier_ref="SUP-M01-DEMO",
            status=ExpectedReceiptStatus.OPEN.value,
        )
    )

    await session.flush()
    return demo_order


# --- Counts ---------------------------------------------------------------


async def _count_tables(session: AsyncSession, organization_id: uuid.UUID) -> dict[str, int]:
    async def _count(stmt: Any) -> int:
        result = await session.execute(stmt)
        return int(result.scalar_one())

    counts: dict[str, int] = {}
    counts["factories"] = await _count(
        select(sa.func.count())
        .select_from(Factory)
        .where(Factory.organization_id == organization_id)
    )
    counts["users"] = await _count(
        select(sa.func.count())
        .select_from(Membership)
        .where(Membership.organization_id == organization_id)
    )
    counts["memberships"] = counts["users"]
    counts["role_assignments"] = await _count(
        select(sa.func.count())
        .select_from(RoleAssignment)
        .join(Membership, RoleAssignment.membership_id == Membership.id)
        .where(Membership.organization_id == organization_id)
    )
    counts["customers"] = await _count(
        select(sa.func.count())
        .select_from(Customer)
        .where(Customer.organization_id == organization_id)
    )
    counts["styles"] = await _count(
        select(sa.func.count()).select_from(Style).where(Style.organization_id == organization_id)
    )
    counts["style_operations"] = await _count(
        select(sa.func.count())
        .select_from(StyleOperation)
        .join(Style, StyleOperation.style_id == Style.id)
        .where(Style.organization_id == organization_id)
    )
    counts["materials"] = await _count(
        select(sa.func.count())
        .select_from(Material)
        .where(Material.organization_id == organization_id)
    )
    counts["bom_versions"] = await _count(
        select(sa.func.count())
        .select_from(BomVersion)
        .join(Style, BomVersion.style_id == Style.id)
        .where(Style.organization_id == organization_id)
    )
    counts["bom_lines"] = await _count(
        select(sa.func.count())
        .select_from(BomLine)
        .join(BomVersion, BomLine.bom_version_id == BomVersion.id)
        .join(Style, BomVersion.style_id == Style.id)
        .where(Style.organization_id == organization_id)
    )
    counts["lines"] = await _count(
        select(sa.func.count()).select_from(Line).where(Line.organization_id == organization_id)
    )
    counts["line_capabilities"] = await _count(
        select(sa.func.count())
        .select_from(LineCapability)
        .join(Line, LineCapability.line_id == Line.id)
        .where(Line.organization_id == organization_id)
    )
    counts["capacity_slots"] = await _count(
        select(sa.func.count())
        .select_from(LineCapacitySlot)
        .where(LineCapacitySlot.organization_id == organization_id)
    )
    counts["orders"] = await _count(
        select(sa.func.count()).select_from(Order).where(Order.organization_id == organization_id)
    )
    counts["allocations"] = await _count(
        select(sa.func.count())
        .select_from(Allocation)
        .where(Allocation.organization_id == organization_id)
    )
    counts["material_lots"] = await _count(
        select(sa.func.count())
        .select_from(MaterialLot)
        .where(MaterialLot.organization_id == organization_id)
    )
    counts["stock_movements"] = await _count(
        select(sa.func.count())
        .select_from(StockMovement)
        .where(StockMovement.organization_id == organization_id)
    )
    counts["material_balances"] = await _count(
        select(sa.func.count())
        .select_from(MaterialBalance)
        .where(MaterialBalance.organization_id == organization_id)
    )
    counts["reservations"] = await _count(
        select(sa.func.count())
        .select_from(Reservation)
        .where(Reservation.organization_id == organization_id)
    )
    counts["expected_receipts"] = await _count(
        select(sa.func.count())
        .select_from(ExpectedReceipt)
        .where(ExpectedReceipt.organization_id == organization_id)
    )
    counts["operator_aliases"] = await _count(
        select(sa.func.count())
        .select_from(OperatorAlias)
        .where(OperatorAlias.organization_id == organization_id)
    )
    counts["skill_records"] = await _count(
        select(sa.func.count())
        .select_from(SkillRecord)
        .join(OperatorAlias, SkillRecord.operator_alias_id == OperatorAlias.id)
        .where(OperatorAlias.organization_id == organization_id)
    )
    counts["operation_staffing"] = await _count(
        select(sa.func.count())
        .select_from(OperationStaffing)
        .where(OperationStaffing.organization_id == organization_id)
    )
    counts["cycle_observations"] = await _count(
        select(sa.func.count())
        .select_from(CycleObservation)
        .where(CycleObservation.organization_id == organization_id)
    )
    counts["line_measurements"] = await _count(
        select(sa.func.count())
        .select_from(LineMeasurement)
        .where(LineMeasurement.organization_id == organization_id)
    )
    counts["quality_policy_versions"] = await _count(
        select(sa.func.count())
        .select_from(QualityPolicyVersion)
        .where(QualityPolicyVersion.organization_id == organization_id)
    )
    counts["inspections"] = await _count(
        select(sa.func.count())
        .select_from(Inspection)
        .where(Inspection.organization_id == organization_id)
    )
    counts["defect_observations"] = await _count(
        select(sa.func.count())
        .select_from(DefectObservation)
        .join(Inspection, DefectObservation.inspection_id == Inspection.id)
        .where(Inspection.organization_id == organization_id)
    )
    counts["quality_holds"] = await _count(
        select(sa.func.count())
        .select_from(QualityHold)
        .where(QualityHold.organization_id == organization_id)
    )
    counts["quality_releases"] = await _count(
        select(sa.func.count())
        .select_from(QualityRelease)
        .where(QualityRelease.organization_id == organization_id)
    )
    return counts
