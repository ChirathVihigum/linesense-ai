"""Integration tests for `app.api.summaries` (task-18-brief.md requirement 4)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Order, RunEvent, RunSnapshot
from app.domain.clock import utcnow
from app.domain.vocab import RunStatus
from app.nlp.summarize import REJECTED_LABEL
from tests.factories import make_order, make_run
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

REPORT_EVENT_TYPE = "run.report"


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


def _report(order: Order, *, eligible: bool = True) -> dict[str, Any]:
    return {
        "order": {
            "id": str(order.id),
            "external_ref": order.external_ref,
            "due_date": "2026-10-01",
        },
        "states": {
            "production": order.production_state,
            "material": order.material_state,
            "quality": order.quality_state,
            "analysis": "COMPLETED",
        },
        "shipment": {
            "eligible": eligible,
            "reasons": [] if eligible else ["Quality pending — no inspection recorded"],
            "source": "Calculated from records",
        },
        "blockers": [
            {
                "code": "MATERIAL_SHORTAGE",
                "message": "M03 is short by 40 m.",
                "agent": "rm",
                "severity": "critical",
                "evidence_ids": ["ev-1"],
                "source": "deterministic",
            }
        ],
        "agent_summaries": [],
        "recommendation": None,
        "evidence": [{"evidence_id": "ev-1", "kind": "record", "description": "material balance"}],
        "degraded": False,
        "degraded_reasons": [],
        "generated_at": utcnow().isoformat(),
    }


async def _completed_run_with_report(session: AsyncSession, order: Order, *, eligible: bool = True):
    run = await make_run(
        session,
        order=order,
        status=RunStatus.COMPLETED.value,
        completed_at=utcnow(),
    )
    session.add(
        RunEvent(
            run_id=run.id,
            event_type=REPORT_EVENT_TYPE,
            actor="orchestrator",
            payload=_report(order, eligible=eligible),
        )
    )
    await session.flush()
    return run


async def test_no_completed_run_returns_conflict(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/orders/{order.id}/status-summary")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_deterministic_summary_is_returned_and_fresh(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    run = await _completed_run_with_report(db_session, order, eligible=False)
    snapshot = RunSnapshot(
        run_id=run.id,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        input_versions={"order": {str(order.id): order.version}},
        data={},
    )
    db_session.add(snapshot)
    await db_session.flush()
    run.snapshot_id = snapshot.id
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/orders/{order.id}/status-summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary_source"] == "deterministic"
    assert body["report_run_id"] == str(run.id)
    assert body["stale"] is False
    assert body["label"] is None
    assert body["sentences"]
    all_ids = {"ev-1"}
    for sentence in body["sentences"]:
        assert all(eid in all_ids for eid in sentence["evidence_ids"])
    assert any("not eligible" in s["text"] for s in body["sentences"])


async def test_summary_is_stale_when_order_version_moved_past_the_snapshot(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    run = await _completed_run_with_report(db_session, order)
    snapshot = RunSnapshot(
        run_id=run.id,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        input_versions={"order": {str(order.id): order.version}},
        data={},
    )
    db_session.add(snapshot)
    await db_session.flush()
    run.snapshot_id = snapshot.id
    order.version = order.version + 1
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/orders/{order.id}/status-summary")
    assert response.status_code == 200
    assert response.json()["stale"] is True


async def test_unknown_order_returns_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/orders/{uuid.uuid4()}/status-summary")
    assert response.status_code == 404


async def test_model_mode_requires_analysis_run_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await _completed_run_with_report(db_session, order)
    await db_session.commit()

    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    response = await storekeeper.get(
        f"/api/v1/orders/{order.id}/status-summary", params={"mode": "model"}
    )
    assert response.status_code == 403


async def test_model_mode_with_default_fixture_provider_falls_back_and_is_capped(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    """The test app's default `LS_LLM_PROVIDER=fixture` client returns no
    scripted JSON sentences for this prompt, so `mode=model` validates,
    rejects, and falls back to the deterministic summary — while still
    writing the `summary.model_generated` audit event that counts against
    the per-order 24h cap (task-18-brief.md requirement 4).
    """
    ktn = identity.factories["KTN"]
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await _completed_run_with_report(db_session, order)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")

    for _ in range(10):
        response = await planner.get(
            f"/api/v1/orders/{order.id}/status-summary", params={"mode": "model"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary_source"] == "deterministic"
        assert body["label"] == REJECTED_LABEL

    capped = await planner.get(
        f"/api/v1/orders/{order.id}/status-summary", params={"mode": "model"}
    )
    assert capped.status_code == 429
    assert capped.json()["error"]["code"] == "RATE_LIMITED"
