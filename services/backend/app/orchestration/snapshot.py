"""Immutable per-run input snapshots (``run_snapshots``).

A snapshot captures every record an analysis run's agents may reason about,
built from the deterministic domain services in one read, plus the versions
of the mutable inputs (``input_versions``) so a later recommendation can be
checked for staleness. Agents read only the snapshot, never live tables.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BomLine,
    BomVersion,
    Customer,
    ExpectedReceipt,
    Factory,
    Line,
    LineCapability,
    LineCapacitySlot,
    Material,
    MaterialBalance,
    Order,
    Style,
    StyleOperation,
)
from app.domain.clock import today_in
from app.domain.ie.service import line_style_analysis
from app.domain.inventory.queries import CONSUMPTION_WINDOW_DAYS, daily_issues
from app.domain.quality import service as quality_service
from app.domain.quality.calc import shipment_eligibility
from app.domain.vocab import ExpectedReceiptStatus, QualityHoldStatus

# --------------------------------------------------------------------------
# Snapshot schema
# --------------------------------------------------------------------------


class SnapshotOrder(BaseModel):
    id: uuid.UUID
    version: int
    external_ref: str
    customer_code: str
    style_id: uuid.UUID
    style_code: str
    quantity: int
    produced_units: int
    remaining_units: int
    due_date: date
    priority: int
    production_state: str
    factory_timezone: str


class SnapshotBomLine(BaseModel):
    bom_line_id: uuid.UUID
    material_id: uuid.UUID
    material_code: str
    material_name: str
    material_unit: str
    bom_unit: str
    quantity_per_unit: Decimal
    wastage_fraction: Decimal
    safety_stock: Decimal
    lead_time_days: int
    pack_size: Decimal | None


class SnapshotBom(BaseModel):
    bom_version_id: uuid.UUID
    version_no: int
    lines: list[SnapshotBomLine]


class SnapshotReceipt(BaseModel):
    id: uuid.UUID
    quantity: Decimal
    expected_date: date


class SnapshotIssue(BaseModel):
    date: date
    quantity: Decimal


class SnapshotMaterial(BaseModel):
    balance_id: uuid.UUID | None
    balance_version: int | None
    on_hand_accepted: Decimal
    reserved: Decimal
    open_receipts: list[SnapshotReceipt]
    issues_14d: list[SnapshotIssue]


class SnapshotOperation(BaseModel):
    operation_id: uuid.UUID
    sequence: int
    code: str
    name: str
    sam_minutes: Decimal
    skill_code: str


class SnapshotLine(BaseModel):
    line_id: uuid.UUID
    code: str
    name: str
    operator_count: int
    compatible: bool
    missing_skills: list[str]


class SnapshotSlot(BaseModel):
    slot_id: uuid.UUID
    line_id: uuid.UUID
    line_code: str
    slot_date: date
    shift_code: str
    available_operator_minutes: Decimal
    planned_efficiency: Decimal
    allocated_standard_minutes: Decimal
    version: int


class SnapshotShipment(BaseModel):
    eligible: bool
    reasons: list[str]


class SnapshotQuality(BaseModel):
    policy: dict[str, Any] | None
    inspections: list[dict[str, Any]]
    active_holds: list[dict[str, Any]]
    releases: list[dict[str, Any]]
    shipment: SnapshotShipment
    quality_state: str


class SnapshotData(BaseModel):
    order: SnapshotOrder
    as_of_date: date
    bom: SnapshotBom
    materials: dict[str, SnapshotMaterial]
    operations: list[SnapshotOperation]
    sam_total_minutes: Decimal
    lines: list[SnapshotLine]
    slots: list[SnapshotSlot]
    ie: dict[str, dict[str, Any]]
    quality: SnapshotQuality

    def balance_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(
            material.balance_id
            for material in self.materials.values()
            if material.balance_id is not None
        )

    def slot_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(slot.slot_id for slot in self.slots)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Plain JSON types for a dataclass tree (Decimal as str, like the rest of
    the snapshot)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


async def _bom(session: AsyncSession, order: Order) -> SnapshotBom:
    bom_version = await session.get(BomVersion, order.bom_version_id)
    if bom_version is None:
        raise LookupError(f"order {order.id} references a missing BOM version")
    rows = (
        await session.execute(
            select(BomLine, Material)
            .join(Material, Material.id == BomLine.material_id)
            .where(BomLine.bom_version_id == bom_version.id)
            .order_by(Material.code, BomLine.id)
        )
    ).all()
    return SnapshotBom(
        bom_version_id=bom_version.id,
        version_no=bom_version.version_no,
        lines=[
            SnapshotBomLine(
                bom_line_id=line.id,
                material_id=material.id,
                material_code=material.code,
                material_name=material.name,
                material_unit=material.unit,
                bom_unit=line.unit,
                quantity_per_unit=line.quantity_per_unit,
                wastage_fraction=line.wastage_fraction,
                safety_stock=material.safety_stock,
                lead_time_days=material.lead_time_days,
                pack_size=material.pack_size,
            )
            for line, material in rows
        ],
    )


async def _materials(
    session: AsyncSession, factory: Factory, material_ids: list[uuid.UUID], as_of: date
) -> dict[str, SnapshotMaterial]:
    if not material_ids:
        return {}
    balances = {
        balance.material_id: balance
        for balance in (
            await session.scalars(
                select(MaterialBalance).where(
                    MaterialBalance.factory_id == factory.id,
                    MaterialBalance.material_id.in_(material_ids),
                )
            )
        ).all()
    }
    receipts: dict[uuid.UUID, list[SnapshotReceipt]] = defaultdict(list)
    for receipt in (
        await session.scalars(
            select(ExpectedReceipt)
            .where(
                ExpectedReceipt.factory_id == factory.id,
                ExpectedReceipt.material_id.in_(material_ids),
                ExpectedReceipt.status == ExpectedReceiptStatus.OPEN.value,
            )
            .order_by(ExpectedReceipt.expected_date, ExpectedReceipt.id)
        )
    ).all():
        receipts[receipt.material_id].append(
            SnapshotReceipt(
                id=receipt.id, quantity=receipt.quantity, expected_date=receipt.expected_date
            )
        )
    issues = await daily_issues(session, factory, as_of, material_ids)

    materials: dict[str, SnapshotMaterial] = {}
    for material_id in material_ids:
        balance = balances.get(material_id)
        materials[str(material_id)] = SnapshotMaterial(
            balance_id=balance.id if balance is not None else None,
            balance_version=balance.version if balance is not None else None,
            on_hand_accepted=balance.on_hand_accepted if balance is not None else Decimal(0),
            reserved=balance.reserved if balance is not None else Decimal(0),
            open_receipts=receipts.get(material_id, []),
            issues_14d=[
                SnapshotIssue(date=issued_on, quantity=abs(quantity))
                for issued_on, quantity in issues.get(material_id, [])
                if (as_of - issued_on).days < CONSUMPTION_WINDOW_DAYS and issued_on <= as_of
            ],
        )
    return materials


async def _lines(
    session: AsyncSession, factory_id: uuid.UUID, required_skills: set[str]
) -> list[SnapshotLine]:
    lines = (
        await session.scalars(
            select(Line)
            .where(Line.factory_id == factory_id, Line.is_active.is_(True))
            .order_by(Line.code)
        )
    ).all()
    skills: dict[uuid.UUID, set[str]] = {line.id: set() for line in lines}
    if lines:
        for line_id, skill_code in (
            await session.execute(
                select(LineCapability.line_id, LineCapability.skill_code).where(
                    LineCapability.line_id.in_(list(skills))
                )
            )
        ).all():
            skills[line_id].add(skill_code)
    result: list[SnapshotLine] = []
    for line in lines:
        missing = sorted(required_skills - skills[line.id])
        result.append(
            SnapshotLine(
                line_id=line.id,
                code=line.code,
                name=line.name,
                operator_count=line.operator_count,
                # A style without operations has no routing: no line is compatible
                # (same rule as app.domain.capacity.service.compatible_line_ids).
                compatible=bool(required_skills) and not missing,
                missing_skills=missing,
            )
        )
    return result


async def _slots(
    session: AsyncSession, compatible: list[SnapshotLine], start: date, end: date
) -> list[SnapshotSlot]:
    if not compatible or end < start:
        return []
    codes = {line.line_id: line.code for line in compatible}
    rows = (
        await session.scalars(
            select(LineCapacitySlot)
            .where(
                LineCapacitySlot.line_id.in_(list(codes)),
                LineCapacitySlot.slot_date >= start,
                LineCapacitySlot.slot_date <= end,
            )
            .order_by(
                LineCapacitySlot.slot_date, LineCapacitySlot.shift_code, LineCapacitySlot.line_id
            )
        )
    ).all()
    return [
        SnapshotSlot(
            slot_id=slot.id,
            line_id=slot.line_id,
            line_code=codes[slot.line_id],
            slot_date=slot.slot_date,
            shift_code=slot.shift_code,
            available_operator_minutes=slot.available_operator_minutes,
            planned_efficiency=slot.planned_efficiency,
            allocated_standard_minutes=slot.allocated_standard_minutes,
            version=slot.version,
        )
        for slot in rows
    ]


async def _ie(
    session: AsyncSession, order: Order, compatible: list[SnapshotLine]
) -> dict[str, dict[str, Any]]:
    analyses: dict[str, dict[str, Any]] = {}
    for line in compatible:
        analysis = await line_style_analysis(
            session, order.factory_id, line.line_id, order.style_id
        )
        if any(operation.sample_count > 0 for operation in analysis.operations):
            analyses[str(line.line_id)] = _jsonable(analysis)
    return analyses


async def _quality(session: AsyncSession, order: Order) -> SnapshotQuality:
    policy = await quality_service.active_policy(session, order.organization_id)
    inspections = await quality_service.list_inspections(session, order.id)
    holds = await quality_service.order_holds(session, order.id)
    releases = await quality_service.list_releases(session, order.id)
    defects = await quality_service.defects_by_inspection(
        session, [inspection.id for inspection in inspections]
    )
    eligibility = shipment_eligibility(await quality_service.shipment_facts(session, order))
    return SnapshotQuality(
        policy=(
            {
                "id": str(policy.id),
                "code": policy.code,
                "version_no": policy.version_no,
                "is_demo": policy.is_demo,
                "rules": policy.rules,
            }
            if policy is not None
            else None
        ),
        inspections=[
            {
                "id": str(inspection.id),
                "inspection_type": inspection.inspection_type,
                "inspected_units": inspection.inspected_units,
                "defective_units": inspection.defective_units,
                "result": inspection.result,
                "policy_version_id": str(inspection.policy_version_id),
                "line_id": str(inspection.line_id) if inspection.line_id else None,
                "inspected_at": inspection.inspected_at.isoformat(),
                # Defects belong to an operation and an inspection; no observation
                # carries an operator, so the quality agent can never see one.
                "defects": [
                    {
                        "defect_code": defect.defect_code,
                        "severity": defect.severity,
                        "count": defect.count,
                        "operation_id": (str(defect.operation_id) if defect.operation_id else None),
                    }
                    for defect in defects.get(inspection.id, [])
                ],
            }
            for inspection in inspections
        ],
        active_holds=[
            {
                "id": str(hold.id),
                "reason": hold.reason,
                "inspection_id": str(hold.inspection_id) if hold.inspection_id else None,
                "created_at": hold.created_at.isoformat(),
            }
            for hold in holds
            if hold.status == QualityHoldStatus.ACTIVE.value
        ],
        releases=[
            {
                "id": str(release.id),
                "inspection_id": str(release.inspection_id),
                "policy_version_id": str(release.policy_version_id),
                "released_at": release.released_at.isoformat(),
            }
            for release in releases
        ],
        shipment=SnapshotShipment(eligible=eligibility.eligible, reasons=list(eligibility.reasons)),
        quality_state=order.quality_state,
    )


async def build_snapshot(
    session: AsyncSession, order: Order
) -> tuple[SnapshotData, dict[str, Any]]:
    """Read everything the agents need for ``order`` and the input versions.

    Returns ``(data, input_versions)`` with ``input_versions`` shaped
    ``{"order": {id: version}, "material_balances": {id: version},
    "capacity_slots": {id: version}, "quality_policy": {id: version_no} | {}}``.
    """
    factory = await session.get(Factory, order.factory_id)
    customer = await session.get(Customer, order.customer_id)
    style = await session.get(Style, order.style_id)
    if factory is None or customer is None or style is None:
        raise LookupError(f"order {order.id} references a missing factory, customer or style")
    as_of = today_in(factory.timezone)

    bom = await _bom(session, order)
    materials = await _materials(
        session, factory, list(dict.fromkeys(line.material_id for line in bom.lines)), as_of
    )
    operations = [
        SnapshotOperation(
            operation_id=operation.id,
            sequence=operation.sequence,
            code=operation.code,
            name=operation.name,
            sam_minutes=operation.sam_minutes,
            skill_code=operation.skill_code,
        )
        for operation in (
            await session.scalars(
                select(StyleOperation)
                .where(StyleOperation.style_id == order.style_id)
                .order_by(StyleOperation.sequence)
            )
        ).all()
    ]
    lines = await _lines(session, order.factory_id, {op.skill_code for op in operations})
    compatible = [line for line in lines if line.compatible]
    slots = await _slots(session, compatible, as_of, order.due_date)
    quality = await _quality(session, order)

    data = SnapshotData(
        order=SnapshotOrder(
            id=order.id,
            version=order.version,
            external_ref=order.external_ref,
            customer_code=customer.code,
            style_id=style.id,
            style_code=style.code,
            quantity=order.quantity,
            produced_units=order.produced_units,
            remaining_units=max(0, order.quantity - order.produced_units),
            due_date=order.due_date,
            priority=order.priority,
            production_state=order.production_state,
            factory_timezone=factory.timezone,
        ),
        as_of_date=as_of,
        bom=bom,
        materials=materials,
        operations=operations,
        sam_total_minutes=sum((op.sam_minutes for op in operations), Decimal(0)),
        lines=lines,
        slots=slots,
        ie=await _ie(session, order, compatible),
        quality=quality,
    )
    policy = quality.policy
    input_versions: dict[str, Any] = {
        "order": {str(order.id): order.version},
        "material_balances": {
            str(material.balance_id): material.balance_version
            for material in materials.values()
            if material.balance_id is not None
        },
        "capacity_slots": {str(slot.slot_id): slot.version for slot in slots},
        "quality_policy": {policy["id"]: policy["version_no"]} if policy is not None else {},
    }
    return data, input_versions
