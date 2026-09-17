"""Integration tests for the deterministic synthetic seed generator (Task 6).

Runs against the real PostgreSQL test database (see
``.superpowers/sdd/2026-09-17-linesense-build/implementer-rules.md``:
this task's isolated test database is ``linesense_test_d``).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.base import Base
from app.db.models import (
    Allocation,
    BomLine,
    BomVersion,
    Customer,
    CycleObservation,
    Inspection,
    Line,
    LineCapacitySlot,
    Material,
    MaterialBalance,
    MaterialLot,
    OperationStaffing,
    OperatorAlias,
    Order,
    QualityPolicyVersion,
    QualityRelease,
    Reservation,
    StockMovement,
    Style,
    StyleOperation,
    User,
)
from app.domain.ie.calc import effective_cycle_seconds, line_balance, representative_cycle_seconds
from app.domain.inventory.calc import available_now, coverable_units, gross_demand, shortage
from app.domain.planning.calc import required_standard_minutes
from app.domain.vocab import MaterialLotStatus, ReservationStatus
from app.seed import scenario as demo_scenario
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from app.seed.vocabulary import DEMO_ORDER_REF as VOCAB_DEMO_ORDER_REF

pytestmark = pytest.mark.integration

ANCHOR_DATE = date(2026, 1, 5)
TEST_ISSUER = "https://idp.seed-test.example"

_PERSONAL_NAME_HEURISTICS = ("John", "Mary", "Kumar", "Perera")


async def _truncate_all(engine: AsyncEngine) -> None:
    tables = [t for t in Base.metadata.sorted_tables if t.name != "alembic_version"]
    table_names = ", ".join(f'"{t.name}"' for t in tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


async def test_order_ref_vocabulary_matches_seed_generator() -> None:
    assert DEMO_ORDER_REF == VOCAB_DEMO_ORDER_REF == "PO-DEMO-001"


async def test_seed_twice_is_idempotent(db_session: AsyncSession) -> None:
    first = await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)
    assert first.created is True
    assert first.demo_order_id is not None

    second = await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)
    assert second.created is False
    assert second.organization_id == first.organization_id
    assert second.demo_order_id == first.demo_order_id
    assert second.counts == first.counts


async def test_expected_counts(db_session: AsyncSession) -> None:
    summary = await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)
    counts = summary.counts

    assert counts["customers"] == 26
    assert counts["styles"] == 12
    assert counts["materials"] == 20
    assert counts["lines"] == 9
    assert counts["users"] == 10
    assert counts["orders"] >= 95
    assert counts["capacity_slots"] == 9 * 30 * 2 == 540


async def test_material_balance_invariant(db_session: AsyncSession) -> None:
    await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)

    balances = list((await db_session.execute(select(MaterialBalance))).scalars())
    assert balances

    for balance in balances:
        movement_sum = await db_session.scalar(
            select(sa.func.coalesce(sa.func.sum(StockMovement.quantity), 0))
            .select_from(StockMovement)
            .join(MaterialLot, StockMovement.lot_id == MaterialLot.id)
            .where(
                StockMovement.material_id == balance.material_id,
                StockMovement.factory_id == balance.factory_id,
                MaterialLot.status == MaterialLotStatus.ACCEPTED.value,
            )
        )
        assert Decimal(movement_sum) == balance.on_hand_accepted, balance.material_id

        reservation_sum = await db_session.scalar(
            select(sa.func.coalesce(sa.func.sum(Reservation.quantity), 0))
            .select_from(Reservation)
            .where(
                Reservation.material_id == balance.material_id,
                Reservation.factory_id == balance.factory_id,
                Reservation.status == ReservationStatus.ACTIVE.value,
            )
        )
        assert Decimal(reservation_sum) == balance.reserved, balance.material_id


async def test_capacity_slot_invariant(db_session: AsyncSession) -> None:
    await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)

    slots = list((await db_session.execute(select(LineCapacitySlot))).scalars())
    assert len(slots) == 540

    for slot in slots:
        allocation_sum = await db_session.scalar(
            select(sa.func.coalesce(sa.func.sum(Allocation.standard_minutes), 0))
            .select_from(Allocation)
            .where(Allocation.slot_id == slot.id, Allocation.status == "ACTIVE")
        )
        assert Decimal(allocation_sum) == slot.allocated_standard_minutes
        assert (
            slot.allocated_standard_minutes
            <= slot.available_operator_minutes * slot.planned_efficiency
        )


async def test_no_personal_names_in_seeded_data(db_session: AsyncSession) -> None:
    await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)

    text_columns = (
        User.display_name,
        Customer.name,
        Style.name,
        Material.name,
        Line.name,
        OperatorAlias.alias_code,
    )
    text_values: list[str] = []
    for column in text_columns:
        text_values.extend((await db_session.execute(select(column))).scalars().all())

    for value in text_values:
        for banned in _PERSONAL_NAME_HEURISTICS:
            assert banned not in value, (banned, value)


async def test_demo_order_facts(db_session: AsyncSession) -> None:
    summary = await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)

    order = await db_session.get(Order, summary.demo_order_id)
    assert order is not None
    assert order.external_ref == DEMO_ORDER_REF
    assert order.quantity == demo_scenario.DEMO_ORDER_QUANTITY
    assert order.due_date == ANCHOR_DATE + timedelta(days=demo_scenario.DEMO_ORDER_DUE_OFFSET_DAYS)
    assert order.priority == demo_scenario.DEMO_ORDER_PRIORITY
    assert order.production_state == "VALIDATED"
    assert order.material_state == "UNKNOWN"
    assert order.quality_state == "NOT_INSPECTED"

    customer = await db_session.get(Customer, order.customer_id)
    assert customer is not None
    assert customer.code == demo_scenario.DEMO_CUSTOMER_CODE

    style = await db_session.get(Style, order.style_id)
    assert style is not None
    assert style.code == demo_scenario.DEMO_STYLE_CODE

    bom_lines = list(
        (
            await db_session.execute(
                select(BomLine).where(BomLine.bom_version_id == order.bom_version_id)
            )
        ).scalars()
    )
    material_id_by_code = {
        m.code: m.id
        for m in (await db_session.execute(select(Material))).scalars()
        if m.code == demo_scenario.DEMO_BOM_MATERIAL_CODE
    }
    demo_material_id = material_id_by_code[demo_scenario.DEMO_BOM_MATERIAL_CODE]
    m01_line = next(line for line in bom_lines if line.material_id == demo_material_id)
    assert m01_line.quantity_per_unit == demo_scenario.DEMO_BOM_QUANTITY_PER_UNIT
    assert m01_line.wastage_fraction == demo_scenario.DEMO_BOM_WASTAGE_FRACTION

    balance = await db_session.scalar(
        select(MaterialBalance).where(
            MaterialBalance.factory_id == order.factory_id,
            MaterialBalance.material_id == demo_material_id,
        )
    )
    assert balance is not None
    assert balance.on_hand_accepted == demo_scenario.DEMO_BALANCE_ON_HAND_ACCEPTED
    assert balance.reserved == demo_scenario.DEMO_BALANCE_RESERVED

    available = available_now(balance.on_hand_accepted, balance.reserved)
    demand = gross_demand(
        Decimal(order.quantity), m01_line.quantity_per_unit, m01_line.wastage_fraction
    )
    short = shortage(available, demand)
    coverable = coverable_units(available, m01_line.quantity_per_unit, m01_line.wastage_fraction)

    assert available == demo_scenario.DEMO_EXPECTED_AVAILABLE == Decimal("1100")
    assert demand == demo_scenario.DEMO_EXPECTED_GROSS_DEMAND == Decimal("1260")
    assert short == demo_scenario.DEMO_EXPECTED_SHORTAGE == Decimal("160")
    assert coverable == demo_scenario.DEMO_EXPECTED_COVERABLE_UNITS == Decimal("873")

    # Capacity: L1-L6 remaining standard minutes before the due date comfortably
    # exceed what the demo order would require.
    style_ops = list(
        (
            await db_session.execute(
                select(StyleOperation).where(StyleOperation.style_id == order.style_id)
            )
        ).scalars()
    )
    sam_total = sum((op.sam_minutes for op in style_ops), Decimal("0"))
    required_minutes = required_standard_minutes(order.quantity, sam_total)

    ktn_line_codes = {f"L{i}" for i in range(1, 7)}
    ktn_lines = list(
        (
            await db_session.execute(select(Line).where(Line.factory_id == order.factory_id))
        ).scalars()
    )
    ktn_line_ids = {line.id for line in ktn_lines if line.code in ktn_line_codes}
    window_slots = list(
        (
            await db_session.execute(
                select(LineCapacitySlot).where(
                    LineCapacitySlot.line_id.in_(ktn_line_ids),
                    LineCapacitySlot.slot_date >= ANCHOR_DATE,
                    LineCapacitySlot.slot_date <= order.due_date,
                )
            )
        ).scalars()
    )
    remaining_minutes = sum(
        (
            max(
                Decimal("0"),
                slot.available_operator_minutes * slot.planned_efficiency
                - slot.allocated_standard_minutes,
            )
            for slot in window_slots
        ),
        Decimal("0"),
    )
    assert remaining_minutes >= required_minutes * 5

    # IE: OP-04 is the bottleneck on L2 with an effective cycle ~60s.
    l2 = next(line for line in ktn_lines if line.code == demo_scenario.DEMO_IE_LINE_CODE)
    staffing_rows = list(
        (
            await db_session.execute(
                select(OperationStaffing).where(
                    OperationStaffing.line_id == l2.id, OperationStaffing.style_id == order.style_id
                )
            )
        ).scalars()
    )
    staffing_by_operation_id = {row.operation_id: row.parallel_operators for row in staffing_rows}

    effective_cycles: list[Decimal] = []
    op_codes_in_sequence: list[str] = []
    for op in sorted(style_ops, key=lambda o: o.sequence):
        observations = list(
            (
                await db_session.execute(
                    select(CycleObservation.observed_seconds).where(
                        CycleObservation.line_id == l2.id,
                        CycleObservation.style_id == order.style_id,
                        CycleObservation.operation_id == op.id,
                    )
                )
            ).scalars()
        )
        assert len(observations) >= 5
        representative = representative_cycle_seconds(observations)
        parallel = staffing_by_operation_id[op.id]
        effective_cycles.append(effective_cycle_seconds(representative, parallel))
        op_codes_in_sequence.append(op.code)

    result = line_balance(effective_cycles)
    bottleneck_code = op_codes_in_sequence[result.bottleneck_index]
    assert bottleneck_code == demo_scenario.DEMO_IE_BOTTLENECK_OPERATION_CODE
    assert (
        demo_scenario.DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_LOW
        <= result.bottleneck_effective_seconds
        <= demo_scenario.DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_HIGH
    )


async def test_every_planned_and_in_production_order_has_allocations(
    db_session: AsyncSession,
) -> None:
    """Regression for fix round 1: `_allocate_order` must never leave a
    PLANNED/IN_PRODUCTION/PRODUCTION_COMPLETE order without at least one
    ACTIVE allocation (previously possible when the one rng-chosen line
    had no capacity left, even though a sibling compatible line did).
    """
    await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)

    orders = list((await db_session.execute(select(Order))).scalars())
    assert orders

    demo_material_id = await db_session.scalar(
        select(Material.id).where(Material.code == demo_scenario.DEMO_BOM_MATERIAL_CODE)
    )

    for order in orders:
        if order.production_state in ("PLANNED", "IN_PRODUCTION"):
            allocation_count = await db_session.scalar(
                select(sa.func.count())
                .select_from(Allocation)
                .where(Allocation.order_id == order.id, Allocation.status == "ACTIVE")
            )
            assert allocation_count >= 1, (order.external_ref, order.production_state)

        if order.production_state == "PLANNED":
            bom_lines = list(
                (
                    await db_session.execute(
                        select(BomLine).where(BomLine.bom_version_id == order.bom_version_id)
                    )
                ).scalars()
            )
            for bom_line in bom_lines:
                if bom_line.material_id == demo_material_id:
                    # M01 is deliberately excluded from generic BOM
                    # reservations everywhere — reserved solely for the
                    # demo scenario's one exact reservation.
                    continue
                reservation_count = await db_session.scalar(
                    select(sa.func.count())
                    .select_from(Reservation)
                    .where(
                        Reservation.order_id == order.id,
                        Reservation.material_id == bom_line.material_id,
                        Reservation.status == "ACTIVE",
                    )
                )
                assert reservation_count >= 1, (order.external_ref, bom_line.material_id)

        if order.production_state == "PRODUCTION_COMPLETE":
            assert order.produced_units >= order.quantity, order.external_ref


async def test_seed_is_deterministic_across_fresh_databases(
    db_session: AsyncSession, owner_engine: AsyncEngine
) -> None:
    first = await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)
    assert first.created is True
    digest_one = await _full_digest(db_session)
    await db_session.commit()

    await _truncate_all(owner_engine)

    second = await seed_demo(db_session, anchor_date=ANCHOR_DATE, issuer=TEST_ISSUER)
    assert second.created is True
    digest_two = await _full_digest(db_session)

    assert digest_one == digest_two
    assert all(digest_one)  # every sub-digest is non-empty


async def _full_digest(session: AsyncSession) -> tuple[tuple[object, ...], ...]:
    """A stable digest covering every table this generator writes a
    business-meaningful (non-UUID, non-`created_at`/`updated_at`) timestamp
    or value into, so a regression like fix round 1's `datetime.now()`
    calls (which made `approved_at`/`inspected_at`/`released_at` differ
    between runs) would fail this test.

    Each sub-digest is a sorted tuple of plain values (never a UUID or a
    primary/foreign key) keyed by a business identifier instead
    (`external_ref`, `(style_code, version_no)`, `(policy_code,
    version_no)`), so it is directly comparable across two independent runs
    against two empty databases.
    """
    order_rows = (
        await session.execute(
            select(
                Order.external_ref,
                Order.quantity,
                Order.due_date,
                Order.production_state,
                Order.material_state,
                Order.quality_state,
                Order.priority,
            )
        )
    ).all()
    orders_digest = tuple(
        sorted(
            (row.external_ref, row.quantity, row.due_date.isoformat(), *row[3:])
            for row in order_rows
        )
    )

    bom_rows = (
        await session.execute(
            select(Style.code, BomVersion.version_no, BomVersion.is_active, BomVersion.approved_at)
            .select_from(BomVersion)
            .join(Style, BomVersion.style_id == Style.id)
        )
    ).all()
    bom_digest = tuple(
        sorted(
            (code, version_no, is_active, approved_at.isoformat())
            for code, version_no, is_active, approved_at in bom_rows
        )
    )

    policy_rows = (
        await session.execute(
            select(
                QualityPolicyVersion.code,
                QualityPolicyVersion.version_no,
                QualityPolicyVersion.status,
                QualityPolicyVersion.approved_at,
            )
        )
    ).all()
    policy_digest = tuple(
        sorted(
            (code, version_no, status, approved_at.isoformat())
            for code, version_no, status, approved_at in policy_rows
        )
    )

    inspection_rows = (
        await session.execute(
            select(
                Order.external_ref,
                Inspection.inspection_type,
                Inspection.inspected_units,
                Inspection.defective_units,
                Inspection.result,
                Inspection.inspected_at,
            )
            .select_from(Inspection)
            .join(Order, Inspection.order_id == Order.id)
        )
    ).all()
    inspection_digest = tuple(
        sorted(
            (row.external_ref, *row[1:5], row.inspected_at.isoformat()) for row in inspection_rows
        )
    )

    release_rows = (
        await session.execute(
            select(Order.external_ref, QualityRelease.released_at)
            .select_from(QualityRelease)
            .join(Order, QualityRelease.order_id == Order.id)
        )
    ).all()
    release_digest = tuple(
        sorted((ref, released_at.isoformat()) for ref, released_at in release_rows)
    )

    return (orders_digest, bom_digest, policy_digest, inspection_digest, release_digest)
