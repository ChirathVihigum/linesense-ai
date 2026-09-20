"""Operations dashboard: `GET /api/v1/factories/{factory_id}/dashboard`
(task-22-brief.md requirement 1).

Everything here is deterministic, record-derived aggregation: no LLM call,
no stored "dashboard" row. The whole response is computed in exactly six
SQL statements (a query-count-bound integration test enforces this):

1. the factory scope check (`load_scoped`);
2. orders at risk, merged with the `pending_approvals` count via a
   `json_agg` aggregate (an aggregate query with no `GROUP BY` always
   returns exactly one row, so the independent `pending_approvals` scalar
   subquery is never lost even when there are zero orders at risk);
3. material shortages;
4. active quality holds;
5. active analysis runs;
6. the next-7-days per-line capacity utilization.

`material_shortages` and `capacity_next_7_days` carry `Decimal` quantities
end to end (never routed through JSON, which would coerce them to floats);
`orders_at_risk` has no `Decimal` fields, so folding it into a JSON
aggregate loses no precision.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.schemas.dashboard import (
    ActiveRunOut,
    CapacityLineUtilizationOut,
    DashboardOut,
    DashboardQualityHoldOut,
    MaterialShortageOut,
    OrderAtRiskOut,
)
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    AnalysisRun,
    BomLine,
    BomVersion,
    Customer,
    Factory,
    Line,
    LineCapacitySlot,
    Material,
    MaterialBalance,
    Order,
    QualityHold,
    Recommendation,
    StockMovement,
    Style,
)
from app.db.session import get_db_session
from app.domain.clock import today_in, utcnow
from app.domain.inventory.queries import CONSUMPTION_WINDOW_DAYS, STATUS_SOURCE
from app.domain.vocab import (
    MaterialState,
    MovementType,
    ProductionState,
    QualityHoldStatus,
    RecommendationStatus,
    RunStatus,
)

router = APIRouter(prefix="/api/v1", tags=["dashboard"])

_LIST_LIMIT = 20
_CAPACITY_WINDOW_DAYS = 7
_OPEN_ORDER_EXCLUDED_STATES = (ProductionState.CANCELLED.value, ProductionState.DISPATCHED.value)
_AT_RISK_ORDER_DUE_DAYS = 7
_AT_RISK_MATERIAL_STATES = (
    MaterialState.SHORTAGE.value,
    MaterialState.AT_RISK.value,
    MaterialState.UNKNOWN.value,
)
_EARLY_PRODUCTION_STATES = (ProductionState.DRAFT.value, ProductionState.VALIDATED.value)
_DEMAND_PRODUCTION_STATES = (ProductionState.VALIDATED.value, ProductionState.PLANNED.value)
_ACTIVE_RUN_STATUSES = (
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.AWAITING_REVIEW.value,
)


async def _orders_at_risk_and_pending_approvals(
    session: AsyncSession, factory: Factory, horizon: date
) -> tuple[list[OrderAtRiskOut], int]:
    orders_subq = (
        sa.select(
            Order.id,
            Order.external_ref,
            Customer.code.label("customer_code"),
            Style.code.label("style_code"),
            Order.due_date,
            Order.quantity,
            Order.produced_units,
            Order.priority,
            Order.production_state,
            Order.material_state,
            Order.quality_state,
        )
        .join(Customer, Customer.id == Order.customer_id)
        .join(Style, Style.id == Order.style_id)
        .where(
            Order.factory_id == factory.id,
            Order.due_date <= horizon,
            Order.production_state.notin_(_OPEN_ORDER_EXCLUDED_STATES),
            sa.or_(
                Order.material_state.in_(_AT_RISK_MATERIAL_STATES),
                Order.production_state.in_(_EARLY_PRODUCTION_STATES),
            ),
        )
        .order_by(Order.due_date.asc(), Order.id.asc())
        .limit(_LIST_LIMIT)
        .subquery()
    )
    order_object = sa.func.json_build_object(
        "id",
        orders_subq.c.id,
        "external_ref",
        orders_subq.c.external_ref,
        "customer_code",
        orders_subq.c.customer_code,
        "style_code",
        orders_subq.c.style_code,
        "due_date",
        orders_subq.c.due_date,
        "quantity",
        orders_subq.c.quantity,
        "produced_units",
        orders_subq.c.produced_units,
        "priority",
        orders_subq.c.priority,
        "production_state",
        orders_subq.c.production_state,
        "material_state",
        orders_subq.c.material_state,
        "quality_state",
        orders_subq.c.quality_state,
    )
    orders_json = sa.cast(
        sa.func.coalesce(
            sa.func.json_agg(
                aggregate_order_by(order_object, orders_subq.c.due_date, orders_subq.c.id)
            ),
            sa.text("'[]'::json"),
        ),
        JSONB,
    )
    pending_subq = (
        sa.select(sa.func.count())
        .select_from(Recommendation)
        .where(
            Recommendation.factory_id == factory.id,
            Recommendation.status == RecommendationStatus.PROPOSED.value,
        )
        .scalar_subquery()
    )
    row = (
        await session.execute(
            sa.select(
                orders_json.label("orders_json"), pending_subq.label("pending_approvals")
            ).select_from(orders_subq)
        )
    ).one()
    orders = [OrderAtRiskOut.model_validate(item) for item in row.orders_json]
    return orders, int(row.pending_approvals or 0)


async def _material_shortages(session: AsyncSession, factory: Factory) -> list[MaterialShortageOut]:
    window_start = utcnow() - timedelta(days=CONSUMPTION_WINDOW_DAYS)
    demand_subq = (
        sa.select(
            BomLine.material_id.label("material_id"),
            sa.func.sum(
                BomLine.quantity_per_unit
                * (Decimal(1) + BomLine.wastage_fraction)
                * (Order.quantity - Order.produced_units)
            ).label("demand"),
        )
        .select_from(Order)
        .join(BomVersion, BomVersion.id == Order.bom_version_id)
        .join(BomLine, BomLine.bom_version_id == BomVersion.id)
        .where(
            Order.factory_id == factory.id, Order.production_state.in_(_DEMAND_PRODUCTION_STATES)
        )
        .group_by(BomLine.material_id)
        .subquery()
    )
    consumption_subq = (
        sa.select(
            StockMovement.material_id.label("material_id"),
            sa.func.sum(sa.func.abs(StockMovement.quantity)).label("issued"),
        )
        .where(
            StockMovement.factory_id == factory.id,
            StockMovement.movement_type == MovementType.ISSUE.value,
            StockMovement.created_at >= window_start,
        )
        .group_by(StockMovement.material_id)
        .subquery()
    )
    available_expr = sa.func.coalesce(MaterialBalance.on_hand_accepted, 0) - sa.func.coalesce(
        MaterialBalance.reserved, 0
    )
    demand_expr = sa.func.coalesce(demand_subq.c.demand, 0)
    daily_expr = sa.func.coalesce(consumption_subq.c.issued, 0) / CONSUMPTION_WINDOW_DAYS
    reorder_expr = daily_expr * Material.lead_time_days + Material.safety_stock
    stmt = (
        sa.select(
            Material.id.label("material_id"),
            Material.code.label("material_code"),
            Material.name.label("material_name"),
            Material.unit,
            available_expr.label("available_now"),
            demand_expr.label("demand"),
            reorder_expr.label("reorder_point"),
        )
        .select_from(Material)
        .outerjoin(
            MaterialBalance,
            sa.and_(
                MaterialBalance.material_id == Material.id, MaterialBalance.factory_id == factory.id
            ),
        )
        .outerjoin(demand_subq, demand_subq.c.material_id == Material.id)
        .outerjoin(consumption_subq, consumption_subq.c.material_id == Material.id)
        .where(Material.organization_id == factory.organization_id)
        .where(sa.or_(available_expr < demand_expr, available_expr < reorder_expr))
        .order_by((demand_expr - available_expr).desc(), Material.code.asc())
        .limit(_LIST_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    return [
        MaterialShortageOut(
            material_id=row.material_id,
            material_code=row.material_code,
            material_name=row.material_name,
            unit=row.unit,
            available_now=row.available_now,
            demand=row.demand,
            reorder_point=row.reorder_point,
            below_reorder_point=row.available_now < row.reorder_point,
            shortage_qty=max(Decimal(0), row.demand - row.available_now),
        )
        for row in rows
    ]


async def _quality_holds(session: AsyncSession, factory: Factory) -> list[DashboardQualityHoldOut]:
    stmt = (
        sa.select(QualityHold, Order.external_ref)
        .join(Order, Order.id == QualityHold.order_id)
        .where(
            QualityHold.factory_id == factory.id,
            QualityHold.status == QualityHoldStatus.ACTIVE.value,
        )
        .order_by(QualityHold.created_at.desc(), QualityHold.id.desc())
        .limit(_LIST_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    return [
        DashboardQualityHoldOut(
            id=hold.id,
            order_id=hold.order_id,
            order_external_ref=external_ref,
            reason=hold.reason,
            created_at=hold.created_at,
        )
        for hold, external_ref in rows
    ]


async def _active_runs(session: AsyncSession, factory: Factory) -> list[ActiveRunOut]:
    stmt = (
        sa.select(AnalysisRun, Order.external_ref)
        .join(Order, Order.id == AnalysisRun.order_id)
        .where(AnalysisRun.factory_id == factory.id, AnalysisRun.status.in_(_ACTIVE_RUN_STATUSES))
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(_LIST_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ActiveRunOut(
            id=run.id,
            order_id=run.order_id,
            order_external_ref=external_ref,
            status=run.status,
            model_calls_used=run.model_calls_used,
            model_calls_limit=run.model_calls_limit,
            started_at=run.started_at,
            deadline_at=run.deadline_at,
        )
        for run, external_ref in rows
    ]


async def _capacity_next_7_days(
    session: AsyncSession, factory: Factory, as_of: date
) -> list[CapacityLineUtilizationOut]:
    window_end = as_of + timedelta(days=_CAPACITY_WINDOW_DAYS - 1)
    stmt = (
        sa.select(
            Line.id.label("line_id"),
            Line.code.label("line_code"),
            Line.name.label("line_name"),
            sa.func.coalesce(
                sa.func.sum(
                    LineCapacitySlot.available_operator_minutes
                    * LineCapacitySlot.planned_efficiency
                ),
                0,
            ).label("capacity_minutes"),
            sa.func.coalesce(sa.func.sum(LineCapacitySlot.allocated_standard_minutes), 0).label(
                "allocated_minutes"
            ),
        )
        .select_from(Line)
        .outerjoin(
            LineCapacitySlot,
            sa.and_(
                LineCapacitySlot.line_id == Line.id,
                LineCapacitySlot.slot_date >= as_of,
                LineCapacitySlot.slot_date <= window_end,
            ),
        )
        .where(Line.factory_id == factory.id, Line.is_active.is_(True))
        .group_by(Line.id, Line.code, Line.name)
        .order_by(Line.code.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        CapacityLineUtilizationOut(
            line_id=row.line_id,
            line_code=row.line_code,
            line_name=row.line_name,
            capacity_minutes=row.capacity_minutes,
            allocated_minutes=row.allocated_minutes,
            utilization_fraction=(
                (row.allocated_minutes / row.capacity_minutes) if row.capacity_minutes > 0 else None
            ),
        )
        for row in rows
    ]


@router.get("/factories/{factory_id}/dashboard", response_model=DashboardOut)
async def get_dashboard(
    factory_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> DashboardOut:
    factory = await load_scoped(session, Factory, factory_id, principal, "order:read")
    as_of = today_in(factory.timezone)
    horizon = as_of + timedelta(days=_AT_RISK_ORDER_DUE_DAYS)

    orders_at_risk, pending_approvals = await _orders_at_risk_and_pending_approvals(
        session, factory, horizon
    )
    material_shortages = await _material_shortages(session, factory)
    quality_holds = await _quality_holds(session, factory)
    active_runs = await _active_runs(session, factory)
    capacity_next_7_days = await _capacity_next_7_days(session, factory, as_of)

    return DashboardOut(
        generated_at=utcnow(),
        status_source=STATUS_SOURCE,
        as_of=as_of,
        orders_at_risk=orders_at_risk,
        material_shortages=material_shortages,
        quality_holds=quality_holds,
        active_runs=active_runs,
        pending_approvals=pending_approvals,
        capacity_next_7_days=capacity_next_7_days,
    )
