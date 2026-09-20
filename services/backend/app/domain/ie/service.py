"""Industrial engineering commands and queries: cycle observations, outlier
marking, operator aliases, and line/style bottleneck analysis (task-9-brief.md).

Database access lives here (``app.domain.ie.calc`` stays pure). Every write
happens inside the caller's transaction; nothing here commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    CycleObservation,
    Factory,
    Line,
    LineCapacitySlot,
    LineMeasurement,
    OperationStaffing,
    OperatorAlias,
    Style,
    StyleOperation,
)
from app.domain.clock import today_in, utcnow
from app.domain.ie.calc import (
    LineBalanceResult,
    effective_cycle_seconds,
    line_balance,
    observed_units_per_hour,
    representative_cycle_seconds,
    sam_capacity_units_per_hour,
)
from app.domain.vocab import ActorType, AuditOutcome

MIN_SAMPLES = 3
CAPACITY_WINDOW_DAYS = 7
MAX_OBSERVED_SECONDS = Decimal(3600)

# Exact wording required by task-9-brief.md.
ASSUMPTIONS: tuple[str, ...] = (
    "Steady flow between sequential operations",
    "Operators on an operation are comparably skilled",
    "No unmodelled machine or material constraint",
    "Balance index is this model's index, not a universal KPI",
)


@dataclass(frozen=True)
class NewObservationInput:
    line_id: uuid.UUID
    style_id: uuid.UUID
    operation_id: uuid.UUID
    operator_alias_code: str
    observed_seconds: Decimal
    observed_at: datetime


@dataclass(frozen=True)
class OperationAnalysis:
    operation_id: uuid.UUID
    code: str
    name: str
    sam_minutes: Decimal
    sample_count: int
    representative_seconds: Decimal | None
    parallel_operators: int
    effective_seconds: Decimal | None
    insufficient_samples: bool


@dataclass(frozen=True)
class LineStyleAnalysis:
    line_id: uuid.UUID
    style_id: uuid.UUID
    operations: list[OperationAnalysis]
    balance: LineBalanceResult | None
    observed_units_per_hour: Decimal | None
    sam_units_per_hour: Decimal | None
    assumptions: list[str]
    limitations: list[str]
    data_versions: dict[str, Any]


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        422, "VALIDATION_ERROR", message, field_errors=[{"field": field, "message": message}]
    )


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------


async def record_observation(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    data: NewObservationInput,
) -> CycleObservation:
    """Record one cycle-time observation (``ie:write``, IE engineer only).

    ``observed_seconds`` must be in ``(0, 3600]`` and ``observed_at`` must
    not be in the future; the operator alias must be active in this factory.
    """
    factory = await load_scoped(session, Factory, factory_id, principal, "ie:write")

    if not (0 < data.observed_seconds <= MAX_OBSERVED_SECONDS):
        raise _invalid("observed_seconds", "Must be greater than zero and at most 3600 seconds.")
    if data.observed_at > utcnow():
        raise _invalid("observed_at", "Cannot be in the future.")

    line = await session.get(Line, data.line_id)
    if line is None or line.factory_id != factory.id:
        raise _invalid("line_id", "Unknown line for this factory.")
    style = await session.get(Style, data.style_id)
    if style is None or style.organization_id != factory.organization_id:
        raise _invalid("style_id", "Unknown style.")
    operation = await session.get(StyleOperation, data.operation_id)
    if operation is None or operation.style_id != style.id:
        raise _invalid("operation_id", "Unknown operation for this style.")

    alias = await session.scalar(
        select(OperatorAlias).where(
            OperatorAlias.factory_id == factory.id,
            OperatorAlias.alias_code == data.operator_alias_code,
        )
    )
    if alias is None or not alias.is_active:
        raise _invalid("operator_alias_code", "Unknown or inactive operator alias.")

    observation = CycleObservation(
        organization_id=factory.organization_id,
        factory_id=factory.id,
        line_id=line.id,
        style_id=style.id,
        operation_id=operation.id,
        operator_alias_id=alias.id,
        observed_seconds=data.observed_seconds,
        observed_at=data.observed_at,
        recorded_by=principal.user_id,
    )
    session.add(observation)
    await session.flush()

    await record_audit(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="ie.observation.record",
        target_type="cycle_observation",
        target_id=str(observation.id),
        outcome=AuditOutcome.SUCCESS.value,
        after={
            "line_id": str(line.id),
            "style_id": str(style.id),
            "operation_id": str(operation.id),
            "operator_alias_id": str(alias.id),
            "observed_seconds": str(observation.observed_seconds),
        },
    )
    return observation


async def mark_outlier(
    session: AsyncSession,
    principal: Principal,
    observation_id: uuid.UUID,
    reason: str | None,
) -> CycleObservation:
    """Mark a cycle observation as an outlier (``ie:write``); it is then
    excluded from `line_style_analysis`'s representative cycles."""
    observation = await load_scoped(
        session, CycleObservation, observation_id, principal, "ie:write"
    )
    observation.is_outlier = True
    observation.outlier_approved_by = principal.user_id
    await session.flush()

    await record_audit(
        session,
        organization_id=observation.organization_id,
        factory_id=observation.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="ie.observation.mark_outlier",
        target_type="cycle_observation",
        target_id=str(observation.id),
        outcome=AuditOutcome.SUCCESS.value,
        reason=reason,
        after={"is_outlier": True, "outlier_approved_by": str(principal.user_id)},
    )
    return observation


async def list_operator_aliases(
    session: AsyncSession, factory_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[OperatorAlias], int]:
    base = select(OperatorAlias).where(OperatorAlias.factory_id == factory_id)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = list(
        (
            await session.scalars(
                base.order_by(OperatorAlias.alias_code).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, int(total or 0)


# --------------------------------------------------------------------------
# Line/style bottleneck analysis
# --------------------------------------------------------------------------


async def _average_planned_efficiency(session: AsyncSession, line: Line) -> Decimal | None:
    """Average `planned_efficiency` of ``line``'s next 7 days of capacity
    slots (today's factory-local date through +6 days); `None` when no
    slots are planned in that window (the caller decides the fallback and
    must surface it as a limitation, since it is an assumption, not a fact)."""
    factory = await session.get(Factory, line.factory_id)
    today = today_in(factory.timezone) if factory is not None else utcnow().date()
    end = today + timedelta(days=CAPACITY_WINDOW_DAYS - 1)
    average = await session.scalar(
        select(sa.func.avg(LineCapacitySlot.planned_efficiency)).where(
            LineCapacitySlot.line_id == line.id,
            LineCapacitySlot.slot_date >= today,
            LineCapacitySlot.slot_date <= end,
        )
    )
    return Decimal(average) if average is not None else None


async def _observed_units_per_hour(
    session: AsyncSession, line: Line, style: Style, window_days: int
) -> Decimal | None:
    """Actual `units_output / hours` from `line_measurements` over the
    window; `None` when there is no measured output to divide."""
    window_start = utcnow().date() - timedelta(days=window_days)
    units_output, hours = (
        await session.execute(
            select(
                sa.func.coalesce(sa.func.sum(LineMeasurement.units_output), 0),
                sa.func.coalesce(sa.func.sum(LineMeasurement.hours), 0),
            ).where(
                LineMeasurement.line_id == line.id,
                LineMeasurement.style_id == style.id,
                LineMeasurement.measured_on >= window_start,
            )
        )
    ).one()
    try:
        return observed_units_per_hour(int(units_output), Decimal(hours))
    except ValueError:
        return None


async def line_style_analysis(
    session: AsyncSession,
    factory_id: uuid.UUID,
    line_id: uuid.UUID,
    style_id: uuid.UUID,
    *,
    window_days: int = 30,
) -> LineStyleAnalysis:
    """Bottleneck and throughput analysis for ``line_id``/``style_id``.

    Scope/permission is the caller's responsibility (``ie:read``, all
    roles); this only checks that the line and style resolve to the given
    factory/organization, raising 404 otherwise.
    """
    line = await session.get(Line, line_id)
    if line is None or line.factory_id != factory_id:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    style = await session.get(Style, style_id)
    if style is None or style.organization_id != line.organization_id:
        raise AppError(404, "NOT_FOUND", "Resource not found.")

    operations = list(
        (
            await session.scalars(
                select(StyleOperation)
                .where(StyleOperation.style_id == style.id)
                .order_by(StyleOperation.sequence)
            )
        ).all()
    )

    staffing_rows = (
        await session.execute(
            select(OperationStaffing.operation_id, OperationStaffing.parallel_operators).where(
                OperationStaffing.line_id == line.id, OperationStaffing.style_id == style.id
            )
        )
    ).all()
    staffing_by_operation: dict[uuid.UUID, int] = {
        operation_id: parallel_operators for operation_id, parallel_operators in staffing_rows
    }

    window_start = utcnow() - timedelta(days=window_days)
    observed_by_operation: dict[uuid.UUID, list[Decimal]] = {}
    for operation_id, seconds in (
        await session.execute(
            select(CycleObservation.operation_id, CycleObservation.observed_seconds).where(
                CycleObservation.line_id == line.id,
                CycleObservation.style_id == style.id,
                CycleObservation.is_outlier.is_(False),
                CycleObservation.observed_at >= window_start,
            )
        )
    ).all():
        observed_by_operation.setdefault(operation_id, []).append(seconds)

    limitations: list[str] = []
    analyses: list[OperationAnalysis] = []
    effective_cycles: list[Decimal] = []
    every_operation_has_representative = bool(operations)

    for operation in operations:
        samples = observed_by_operation.get(operation.id, [])
        parallel_operators = staffing_by_operation.get(operation.id, 1)
        sample_count = len(samples)
        representative: Decimal | None
        effective: Decimal | None
        if sample_count >= MIN_SAMPLES:
            representative = representative_cycle_seconds(samples, min_samples=MIN_SAMPLES)
            effective = effective_cycle_seconds(representative, parallel_operators)
            effective_cycles.append(effective)
            insufficient = False
        else:
            representative = None
            effective = None
            insufficient = True
            every_operation_has_representative = False
            limitations.append(
                f"Operation {operation.code}: fewer than {MIN_SAMPLES} recent cycle "
                f"observations (has {sample_count})."
            )
        analyses.append(
            OperationAnalysis(
                operation_id=operation.id,
                code=operation.code,
                name=operation.name,
                sam_minutes=operation.sam_minutes,
                sample_count=sample_count,
                representative_seconds=representative,
                parallel_operators=parallel_operators,
                effective_seconds=effective,
                insufficient_samples=insufficient,
            )
        )

    balance = (
        line_balance(effective_cycles)
        if every_operation_has_representative and effective_cycles
        else None
    )

    observed_throughput = await _observed_units_per_hour(session, line, style, window_days)

    sam_minutes_total = sum((operation.sam_minutes for operation in operations), Decimal(0))
    planned_efficiency_avg = await _average_planned_efficiency(session, line)
    if planned_efficiency_avg is None:
        planned_efficiency = Decimal(1)
        limitations.append(
            "No planned capacity slots for this line in the next "
            f"{CAPACITY_WINDOW_DAYS} days: SAM capacity assumes 100% planned "
            "efficiency."
        )
    else:
        planned_efficiency = planned_efficiency_avg
    sam_throughput: Decimal | None
    try:
        sam_throughput = sam_capacity_units_per_hour(
            line.operator_count, sam_minutes_total, planned_efficiency
        )
    except ValueError:
        sam_throughput = None
        limitations.append("SAM capacity could not be computed: the style has no operations.")

    data_versions = {
        "cycle_observations_considered": sum(len(v) for v in observed_by_operation.values()),
        "operation_staffing_rows": len(staffing_by_operation),
        "line_operator_count": line.operator_count,
        "window_days": window_days,
    }

    return LineStyleAnalysis(
        line_id=line.id,
        style_id=style.id,
        operations=analyses,
        balance=balance,
        observed_units_per_hour=observed_throughput,
        sam_units_per_hour=sam_throughput,
        assumptions=list(ASSUMPTIONS),
        limitations=limitations,
        data_versions=data_versions,
    )
