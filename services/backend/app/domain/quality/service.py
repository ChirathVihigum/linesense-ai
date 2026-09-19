"""Quality commands and queries: inspections, holds, separated releases,
defect trends, and shipment facts (task-9-brief.md).

Database access lives here (``app.domain.quality.calc`` stays pure).
`shipment_facts` supersedes the private shipment-fact gathering in
`app.domain.orders.service` (owned by another task); it is not wired into
the orders API yet, but is safe to call independently.

Every write happens inside the caller's transaction; nothing here commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    DefectObservation,
    Inspection,
    Line,
    Order,
    QualityHold,
    QualityPolicyVersion,
    QualityRelease,
)
from app.domain.clock import utcnow
from app.domain.quality.calc import (
    QualityPolicyRules,
    ShipmentFacts,
    defective_rate,
    defects_per_hundred_units,
    evaluate_inspection,
)
from app.domain.quality.calc import quality_state as compute_quality_state
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    DefectSeverity,
    InspectionResult,
    InspectionType,
    PolicyStatus,
    QualityHoldStatus,
)

DEMO_POLICY_CODE = "QP-DEMO"


@dataclass(frozen=True)
class DefectInput:
    defect_code: str
    severity: str
    count: int
    operation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class NewInspectionInput:
    inspection_type: str
    inspected_units: int
    defective_units: int
    defects: list[DefectInput]
    line_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DefectCodeTrend:
    defect_code: str
    severity: str
    count: int


@dataclass(frozen=True)
class DefectTrend:
    factory_id: uuid.UUID
    window_days: int
    inspected_units: int
    defective_units: int
    defective_rate: Decimal | None
    defects_per_hundred_units: Decimal | None
    by_defect_code: list[DefectCodeTrend]


async def _lock_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    """Re-select ``order_id`` under ``FOR UPDATE`` (see
    `app.domain.orders.service._lock_order` for the race it closes)."""
    return (
        await session.scalars(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


async def active_policy(
    session: AsyncSession, organization_id: uuid.UUID, code: str = DEMO_POLICY_CODE
) -> QualityPolicyVersion | None:
    policy: QualityPolicyVersion | None = await session.scalar(
        select(QualityPolicyVersion)
        .where(
            QualityPolicyVersion.organization_id == organization_id,
            QualityPolicyVersion.code == code,
            QualityPolicyVersion.status == PolicyStatus.ACTIVE.value,
        )
        .order_by(QualityPolicyVersion.version_no.desc())
        .limit(1)
    )
    return policy


async def list_policies(
    session: AsyncSession, organization_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[QualityPolicyVersion], int]:
    base = select(QualityPolicyVersion).where(
        QualityPolicyVersion.organization_id == organization_id
    )
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = list(
        (
            await session.scalars(
                base.order_by(QualityPolicyVersion.code, QualityPolicyVersion.version_no.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return rows, int(total or 0)


# --------------------------------------------------------------------------
# Shared read helpers (used by commands and by `shipment_facts`)
# --------------------------------------------------------------------------


async def _latest_inspections_by_type(
    session: AsyncSession, order_id: uuid.UUID
) -> dict[str, Inspection]:
    """The order's latest inspection per `inspection_type`."""
    rows = (
        await session.scalars(
            select(Inspection)
            .where(Inspection.order_id == order_id)
            .order_by(Inspection.inspected_at.desc(), Inspection.id.desc())
        )
    ).all()
    latest: dict[str, Inspection] = {}
    for inspection in rows:
        latest.setdefault(inspection.inspection_type, inspection)
    return latest


async def _latest_final_inspection(session: AsyncSession, order_id: uuid.UUID) -> Inspection | None:
    inspection: Inspection | None = await session.scalar(
        select(Inspection)
        .where(
            Inspection.order_id == order_id,
            Inspection.inspection_type == InspectionType.FINAL.value,
        )
        .order_by(Inspection.inspected_at.desc(), Inspection.id.desc())
        .limit(1)
    )
    return inspection


async def _has_active_hold(session: AsyncSession, order_id: uuid.UUID) -> bool:
    return bool(
        await session.scalar(
            select(
                sa.exists().where(
                    QualityHold.order_id == order_id,
                    QualityHold.status == QualityHoldStatus.ACTIVE.value,
                )
            )
        )
    )


async def _has_valid_release(
    session: AsyncSession, order_id: uuid.UUID, latest_final: Inspection | None
) -> bool:
    """A release is valid only if it references the order's latest FINAL
    inspection: a later FINAL inspection (of any result) invalidates it."""
    if latest_final is None:
        return False
    return bool(
        await session.scalar(
            select(
                sa.exists().where(
                    QualityRelease.order_id == order_id,
                    QualityRelease.inspection_id == latest_final.id,
                )
            )
        )
    )


async def _recompute_quality_state(session: AsyncSession, order: Order) -> None:
    latest_by_type = await _latest_inspections_by_type(session, order.id)
    has_active_hold = await _has_active_hold(session, order.id)
    latest_final = latest_by_type.get(InspectionType.FINAL.value)
    has_valid_release = await _has_valid_release(session, order.id, latest_final)
    order.quality_state = compute_quality_state(
        has_inspection=bool(latest_by_type),
        has_active_hold=has_active_hold,
        has_valid_release=has_valid_release,
    ).value


async def shipment_facts(session: AsyncSession, order: Order) -> ShipmentFacts:
    """Build `ShipmentFacts` straight from the database for a single order.

    Callers pass the result to `app.domain.quality.calc.shipment_eligibility`.
    """
    policy = await active_policy(session, order.organization_id)
    required_types = (
        frozenset(cast("list[str]", policy.rules["required_inspection_types"]))
        if policy is not None
        else frozenset()
    )
    latest_by_type = await _latest_inspections_by_type(session, order.id)
    passed_types = frozenset(
        inspection_type
        for inspection_type, inspection in latest_by_type.items()
        if inspection.result == InspectionResult.PASS_.value
    )
    has_active_hold = await _has_active_hold(session, order.id)
    latest_final = latest_by_type.get(InspectionType.FINAL.value)
    has_valid_release = await _has_valid_release(session, order.id, latest_final)

    return ShipmentFacts(
        production_state=order.production_state,
        quantity=order.quantity,
        packed_units=order.packed_units,
        passed_inspection_types=passed_types,
        required_inspection_types=required_types,
        has_active_hold=has_active_hold,
        has_valid_release=has_valid_release,
        policy_known=policy is not None,
    )


# --------------------------------------------------------------------------
# Inspections
# --------------------------------------------------------------------------


async def record_inspection(
    session: AsyncSession, principal: Principal, order_id: uuid.UUID, data: NewInspectionInput
) -> Inspection:
    """Record an inspection (``quality:inspect``) and evaluate it against the
    organization's currently ACTIVE quality policy.

    A FAIL auto-creates an ACTIVE hold (`created_by=None`, a system rule) and
    `orders.quality_state` is recomputed either way. Raises 409 ``CONFLICT``
    "No approved quality policy" when no policy is ACTIVE (never passes by
    default).
    """
    scoped = await load_scoped(session, Order, order_id, principal, "quality:inspect")
    order = await _lock_order(session, scoped.id)

    if data.defective_units > data.inspected_units:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Defective units cannot exceed inspected units.",
            field_errors=[
                {"field": "defective_units", "message": "Cannot exceed inspected units."}
            ],
        )

    line: Line | None = None
    if data.line_id is not None:
        line = await session.get(Line, data.line_id)
        if line is None or line.factory_id != order.factory_id:
            raise AppError(
                422,
                "VALIDATION_ERROR",
                "Unknown line for this order's factory.",
                field_errors=[
                    {"field": "line_id", "message": "Unknown line for this order's factory."}
                ],
            )

    policy = await active_policy(session, order.organization_id)
    if policy is None:
        raise AppError(409, "CONFLICT", "No approved quality policy")

    rules = QualityPolicyRules.from_json(policy.rules)
    critical_defects = sum(
        defect.count for defect in data.defects if defect.severity == DefectSeverity.CRITICAL.value
    )
    evaluation = evaluate_inspection(
        rules,
        inspected_units=data.inspected_units,
        defective_units=data.defective_units,
        critical_defects=critical_defects,
    )

    inspection = Inspection(
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        line_id=line.id if line is not None else None,
        inspection_type=data.inspection_type,
        inspected_units=data.inspected_units,
        defective_units=data.defective_units,
        policy_version_id=policy.id,
        result=evaluation.result,
        inspected_by=principal.user_id,
        inspected_at=utcnow(),
    )
    session.add(inspection)
    await session.flush()

    for defect in data.defects:
        session.add(
            DefectObservation(
                inspection_id=inspection.id,
                defect_code=defect.defect_code,
                severity=defect.severity,
                count=defect.count,
                operation_id=defect.operation_id,
            )
        )

    if evaluation.result == InspectionResult.FAIL.value:
        session.add(
            QualityHold(
                organization_id=order.organization_id,
                factory_id=order.factory_id,
                order_id=order.id,
                inspection_id=inspection.id,
                reason=f"Automatic hold: {', '.join(evaluation.reasons)}",
                status=QualityHoldStatus.ACTIVE.value,
                created_by=None,
            )
        )

    order.version += 1
    await session.flush()
    await _recompute_quality_state(session, order)
    await session.flush()

    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="quality.inspection.record",
        target_type="inspection",
        target_id=str(inspection.id),
        outcome=AuditOutcome.SUCCESS.value,
        after={
            "order_id": str(order.id),
            "inspection_type": inspection.inspection_type,
            "result": inspection.result,
            "quality_state": order.quality_state,
        },
    )
    return inspection


# --------------------------------------------------------------------------
# Holds
# --------------------------------------------------------------------------


async def place_hold(
    session: AsyncSession, principal: Principal, order_id: uuid.UUID, reason: str
) -> QualityHold:
    """Manually place an ACTIVE hold on an order (``quality:hold``)."""
    scoped = await load_scoped(session, Order, order_id, principal, "quality:hold")
    order = await _lock_order(session, scoped.id)

    if not reason or not reason.strip():
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "A reason is required.",
            field_errors=[{"field": "reason", "message": "A reason is required."}],
        )

    hold = QualityHold(
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        inspection_id=None,
        reason=reason,
        status=QualityHoldStatus.ACTIVE.value,
        created_by=principal.user_id,
    )
    session.add(hold)
    order.version += 1
    await session.flush()
    await _recompute_quality_state(session, order)
    await session.flush()

    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="quality.hold.place",
        target_type="quality_hold",
        target_id=str(hold.id),
        outcome=AuditOutcome.SUCCESS.value,
        reason=reason,
        after={"order_id": str(order.id), "quality_state": order.quality_state},
    )
    return hold


async def list_holds(
    session: AsyncSession, factory_id: uuid.UUID, *, status: str | None, limit: int, offset: int
) -> tuple[list[QualityHold], int]:
    conditions: list[Any] = [QualityHold.factory_id == factory_id]
    if status is not None:
        conditions.append(QualityHold.status == status)
    base = select(QualityHold).where(*conditions)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = list(
        (
            await session.scalars(
                base.order_by(QualityHold.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, int(total or 0)


# --------------------------------------------------------------------------
# Release (separation of duties)
# --------------------------------------------------------------------------


async def release_order(
    session: AsyncSession,
    principal: Principal,
    order_id: uuid.UUID,
    *,
    inspection_id: uuid.UUID,
    expected_order_version: int,
    notes: str | None,
) -> QualityRelease:
    """Release an order for shipment against a passing FINAL inspection
    (``quality:release``).

    The referenced inspection must belong to the order, be FINAL, PASS, be
    the order's *latest* FINAL inspection, and use the currently ACTIVE
    policy version; the releaser must differ from the inspector (403
    ``SELF_APPROVAL_DENIED``, separation of duties). Releases every ACTIVE
    hold and recomputes `quality_state`.
    """
    scoped = await load_scoped(session, Order, order_id, principal, "quality:release")
    order = await _lock_order(session, scoped.id)

    if expected_order_version != order.version:
        raise AppError(409, "STALE_INPUT", "The order has changed since it was loaded.")

    inspection = await session.get(Inspection, inspection_id)
    if inspection is None or inspection.order_id != order.id:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Unknown inspection for this order.",
            field_errors=[
                {"field": "inspection_id", "message": "Unknown inspection for this order."}
            ],
        )
    if (
        inspection.inspection_type != InspectionType.FINAL.value
        or inspection.result != InspectionResult.PASS_.value
    ):
        raise AppError(409, "CONFLICT", "Only a passing FINAL inspection can be released.")

    latest_final = await _latest_final_inspection(session, order.id)
    if latest_final is None or latest_final.id != inspection.id:
        raise AppError(409, "CONFLICT", "This is not the order's latest FINAL inspection.")

    policy = await active_policy(session, order.organization_id)
    if policy is None or inspection.policy_version_id != policy.id:
        raise AppError(
            409, "CONFLICT", "The inspection did not use the currently active quality policy."
        )

    if inspection.inspected_by is not None and inspection.inspected_by == principal.user_id:
        raise AppError(403, "SELF_APPROVAL_DENIED", "The releaser must differ from the inspector.")

    release = QualityRelease(
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        inspection_id=inspection.id,
        policy_version_id=policy.id,
        released_by=principal.user_id,
        released_at=utcnow(),
        notes=notes,
    )
    session.add(release)
    await session.flush()

    active_holds = list(
        (
            await session.scalars(
                select(QualityHold)
                .where(
                    QualityHold.order_id == order.id,
                    QualityHold.status == QualityHoldStatus.ACTIVE.value,
                )
                .with_for_update()
            )
        ).all()
    )
    for hold in active_holds:
        hold.status = QualityHoldStatus.RELEASED.value
        hold.released_by = principal.user_id
        hold.released_at = release.released_at
        hold.release_id = release.id

    order.version += 1
    await session.flush()
    await _recompute_quality_state(session, order)
    await session.flush()

    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="quality.release",
        target_type="quality_release",
        target_id=str(release.id),
        outcome=AuditOutcome.SUCCESS.value,
        reason=notes,
        after={
            "order_id": str(order.id),
            "inspection_id": str(inspection.id),
            "quality_state": order.quality_state,
        },
    )
    return release


async def list_inspections(session: AsyncSession, order_id: uuid.UUID) -> list[Inspection]:
    return list(
        (
            await session.scalars(
                select(Inspection)
                .where(Inspection.order_id == order_id)
                .order_by(Inspection.inspected_at.desc())
            )
        ).all()
    )


async def list_releases(session: AsyncSession, order_id: uuid.UUID) -> list[QualityRelease]:
    return list(
        (
            await session.scalars(
                select(QualityRelease)
                .where(QualityRelease.order_id == order_id)
                .order_by(QualityRelease.released_at.desc())
            )
        ).all()
    )


async def order_holds(session: AsyncSession, order_id: uuid.UUID) -> list[QualityHold]:
    return list(
        (
            await session.scalars(
                select(QualityHold)
                .where(QualityHold.order_id == order_id)
                .order_by(QualityHold.created_at.desc())
            )
        ).all()
    )


async def defects_by_inspection(
    session: AsyncSession, inspection_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[DefectObservation]]:
    if not inspection_ids:
        return {}
    result: dict[uuid.UUID, list[DefectObservation]] = {i: [] for i in inspection_ids}
    rows = (
        await session.scalars(
            select(DefectObservation)
            .where(DefectObservation.inspection_id.in_(inspection_ids))
            .order_by(DefectObservation.defect_code)
        )
    ).all()
    for row in rows:
        result.setdefault(row.inspection_id, []).append(row)
    return result


# --------------------------------------------------------------------------
# Defect trends
# --------------------------------------------------------------------------


async def defect_trends(
    session: AsyncSession, factory_id: uuid.UUID, *, days: int = 30
) -> DefectTrend:
    window_start = utcnow() - timedelta(days=days)
    inspected_units_raw, defective_units_raw = (
        await session.execute(
            select(
                sa.func.coalesce(sa.func.sum(Inspection.inspected_units), 0),
                sa.func.coalesce(sa.func.sum(Inspection.defective_units), 0),
            ).where(Inspection.factory_id == factory_id, Inspection.inspected_at >= window_start)
        )
    ).one()
    inspected_units = int(inspected_units_raw)
    defective_units = int(defective_units_raw)

    by_code_rows = (
        await session.execute(
            select(
                DefectObservation.defect_code,
                DefectObservation.severity,
                sa.func.sum(DefectObservation.count),
            )
            .join(Inspection, Inspection.id == DefectObservation.inspection_id)
            .where(Inspection.factory_id == factory_id, Inspection.inspected_at >= window_start)
            .group_by(DefectObservation.defect_code, DefectObservation.severity)
            .order_by(DefectObservation.defect_code)
        )
    ).all()
    by_defect_code = [
        DefectCodeTrend(defect_code=code, severity=severity, count=int(count))
        for code, severity, count in by_code_rows
    ]
    total_defects = sum(row.count for row in by_defect_code)

    return DefectTrend(
        factory_id=factory_id,
        window_days=days,
        inspected_units=inspected_units,
        defective_units=defective_units,
        defective_rate=defective_rate(defective_units, inspected_units),
        defects_per_hundred_units=defects_per_hundred_units(total_defects, inspected_units),
        by_defect_code=by_defect_code,
    )
