"""Integration tests for `app.api.dashboard` (task-22-brief.md requirement 1)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.sessions import load_principal, resolve_session
from app.db.models import BomLine, QualityHold
from app.domain.clock import today_in
from app.domain.vocab import (
    MaterialState,
    ProductionState,
    QualityHoldStatus,
    QualityState,
    RunStatus,
)
from tests.factories import (
    make_balance,
    make_line,
    make_material,
    make_order,
    make_recommendation,
    make_run,
    make_slot,
)
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _seed_dashboard_data(db_session: AsyncSession, identity: IdentityFixture) -> Any:
    ktn = identity.factories["KTN"]
    as_of = today_in(ktn.timezone)

    at_risk_order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        external_ref="PO-ATRISK",
        due_date=as_of + timedelta(days=2),
        production_state=ProductionState.VALIDATED.value,
        material_state=MaterialState.SHORTAGE.value,
        quality_state=QualityState.NOT_INSPECTED.value,
        quantity=100,
    )
    await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        external_ref="PO-FINE",
        due_date=as_of + timedelta(days=60),
        production_state=ProductionState.PLANNED.value,
        material_state=MaterialState.READY.value,
    )

    material = await make_material(
        db_session,
        organization=identity.organization,
        code="FAB-01",
        safety_stock=D(0),
        lead_time_days=5,
    )
    db_session.add(
        BomLine(
            bom_version_id=at_risk_order.bom_version_id,
            material_id=material.id,
            quantity_per_unit=D("2"),
            unit="m",
            wastage_fraction=D("0"),
        )
    )
    await make_balance(
        db_session,
        organization=identity.organization,
        factory=ktn,
        material=material,
        on_hand_accepted=D("10"),
        reserved=D("0"),
    )

    hold_order = await make_order(
        db_session, organization=identity.organization, factory=ktn, external_ref="PO-HOLD"
    )
    db_session.add(
        QualityHold(
            organization_id=identity.organization.id,
            factory_id=ktn.id,
            order_id=hold_order.id,
            reason="Defect rate above threshold",
            status=QualityHoldStatus.ACTIVE.value,
        )
    )

    run_order = await make_order(
        db_session, organization=identity.organization, factory=ktn, external_ref="PO-RUN"
    )
    planner = identity.users["planner@demo.test"]
    run = await make_run(
        db_session, order=run_order, requested_by=planner, status=RunStatus.RUNNING.value
    )
    supervisor = identity.users["supervisor@demo.test"]
    await make_recommendation(db_session, run=run, proposer=supervisor)

    line = await make_line(db_session, organization=identity.organization, factory=ktn, code="L01")
    for offset in range(3):
        await make_slot(
            db_session,
            line=line,
            slot_date=as_of + timedelta(days=offset),
            shift_code="A",
            available_operator_minutes=D("480"),
            planned_efficiency=D("0.75"),
            allocated_standard_minutes=D("180"),
        )
    # Outside the 7-day window: must not affect the reported utilization.
    await make_slot(
        db_session,
        line=line,
        slot_date=as_of + timedelta(days=30),
        shift_code="A",
        available_operator_minutes=D("480"),
        planned_efficiency=D("0.75"),
        allocated_standard_minutes=D("360"),
    )
    return as_of


async def test_dashboard_reports_all_sections(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    as_of = await _seed_dashboard_data(db_session, identity)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/factories/{ktn.id}/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status_source"] == "Calculated from records"
    assert body["as_of"] == as_of.isoformat()

    risk_refs = {order["external_ref"] for order in body["orders_at_risk"]}
    assert "PO-ATRISK" in risk_refs
    assert "PO-FINE" not in risk_refs

    shortages = {row["material_code"]: row for row in body["material_shortages"]}
    assert "FAB-01" in shortages
    shortage = shortages["FAB-01"]
    assert D(shortage["available_now"]) == D("10")
    assert D(shortage["demand"]) == D("200")
    assert D(shortage["shortage_qty"]) == D("190")

    hold_reasons = {hold["reason"] for hold in body["quality_holds"]}
    assert "Defect rate above threshold" in hold_reasons

    run_refs = {run["order_external_ref"] for run in body["active_runs"]}
    assert "PO-RUN" in run_refs

    assert body["pending_approvals"] >= 1

    capacity = {line["line_code"]: line for line in body["capacity_next_7_days"]}
    assert "L01" in capacity
    line_capacity = capacity["L01"]
    assert D(line_capacity["capacity_minutes"]) == D("1080")
    assert D(line_capacity["allocated_minutes"]) == D("540")
    assert D(line_capacity["utilization_fraction"]) == D("0.5")


async def test_dashboard_is_404_for_a_factory_the_caller_cannot_see(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    byg_planner = await login_as(client, session_factory, "byg.planner@demo.test")
    response = await byg_planner.get(f"/api/v1/factories/{ktn.id}/dashboard")
    assert response.status_code == 404


async def _count_statements(db_engine: AsyncEngine, action: Any) -> int:
    statement_count = 0

    def _count(*_args: Any, **_kwargs: Any) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(db_engine.sync_engine, "before_cursor_execute", _count)
    try:
        await action()
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", _count)
    return statement_count


async def test_dashboard_query_count_is_bounded(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    """The dashboard's own computation never exceeds 6 SQL statements.

    Every authenticated request first pays a fixed, endpoint-independent
    cost to resolve the session and load the principal (`resolve_session` +
    `load_principal`, the same machinery behind every other route in the
    app). That overhead is measured directly here and subtracted, so the
    assertion is about the dashboard's own query budget, not about
    session/CSRF plumbing shared by every endpoint in the API.
    """
    ktn = identity.factories["KTN"]
    await _seed_dashboard_data(db_session, identity)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    token = client.cookies.get("ls_session")
    assert token is not None

    async def _resolve_auth() -> None:
        async with session_factory() as session:
            record = await resolve_session(session, token)
            assert record is not None
            await load_principal(session, record)

    auth_overhead = await _count_statements(db_engine, _resolve_auth)

    async def _call_dashboard() -> None:
        response = await planner.get(f"/api/v1/factories/{ktn.id}/dashboard")
        assert response.status_code == 200

    total = await _count_statements(db_engine, _call_dashboard)

    dashboard_only = total - auth_overhead
    assert dashboard_only <= 6, (
        f"dashboard executed {dashboard_only} SQL statements "
        f"(total {total}, auth overhead {auth_overhead}), expected <= 6"
    )
