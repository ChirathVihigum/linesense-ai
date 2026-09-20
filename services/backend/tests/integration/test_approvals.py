"""Approvals and transactional application (task-14-brief.md).

Every test drives the real flow through the public API: the Task 13 agents
produce the proposal, a supervisor decides it, and a *different* person
applies it inside one fenced transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import (
    Allocation,
    Approval,
    AuditEvent,
    IdempotencyKey,
    Job,
    LineCapacitySlot,
    Material,
    MaterialBalance,
    MaterialLot,
    Notification,
    Order,
    Recommendation,
    Reservation,
    User,
)
from app.domain.vocab import (
    AllocationStatus,
    MaterialLotStatus,
    ProductionState,
    RecommendationStatus,
    ReservationStatus,
)
from app.seed import scenario as demo
from app.seed.generator import DEMO_ORDER_REF
from app.settings import Settings
from tests.factories import make_line, make_order, make_slot
from tests.helpers.approvals import build_approved, key, propose
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


async def _balance(session: AsyncSession, factory_id: uuid.UUID, code: str) -> MaterialBalance:
    balance = await session.scalar(
        sa.select(MaterialBalance)
        .join(Material, Material.id == MaterialBalance.material_id)
        .where(MaterialBalance.factory_id == factory_id, Material.code == code)
    )
    assert balance is not None
    return balance


async def _actions(session: AsyncSession, target_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = (
        await session.execute(
            sa.select(AuditEvent.action, AuditEvent.outcome)
            .where(AuditEvent.target_id == str(target_id))
            .order_by(AuditEvent.id)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def test_supervisor_approves_then_applies_the_demo_recommendation(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)

    async with session_factory() as session:
        before_balance = await _balance(session, proposal.factory_id, demo.DEMO_BOM_MATERIAL_CODE)
        reserved_before = before_balance.reserved
        order_before = await session.get(Order, proposal.order_id)
        assert order_before is not None
        order_version_before = order_before.version

    supervisor_client = AsyncClient(
        transport=client._transport,  # noqa: SLF001 - second identity needs its own cookie jar
        base_url="http://testserver",
    )
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")

    listed = await supervisor.get(
        f"/api/v1/factories/{proposal.factory_id}/recommendations?status=PROPOSED"
    )
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["items"]] == [str(proposal.recommendation_id)]
    # Which label depends on whether the planning summary came from the model;
    # that is Task 13's call, so assert only that the two agree.
    listed_row = listed.json()["items"][0]
    assert listed_row["status_source"] == (
        "AI recommendation" if listed_row["generated_by"] == "model" else "Calculated from records"
    )

    detail = await supervisor.get(f"/api/v1/recommendations/{proposal.recommendation_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["stale"] is False
    assert body["expired"] is False
    assert body["can_decide"] is True
    assert body["decide_blocked_reason"] is None
    assert body["order"]["external_ref"] == DEMO_ORDER_REF
    assert body["run"]["id"] == str(proposal.run_id)
    assert body["run"]["llm"]["is_fixture"] is True
    assert body["evidence"], "the proposal must carry resolved evidence"
    assert body["diff"]["slots"], "the proposal must diff every slot it touches"
    reservation_diff = next(
        row
        for row in body["diff"]["reservations"]
        if row["material_code"] == demo.DEMO_BOM_MATERIAL_CODE
    )
    assert Decimal(reservation_diff["reserved_after"]) - Decimal(
        reservation_diff["reserved_before"]
    ) == Decimal("1099.98")

    decided = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={
            "decision": "APPROVED",
            "reason": "Capacity and stock both check out.",
            "proposal_hash": proposal.proposal_hash,
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == RecommendationStatus.APPROVED.value

    after_decision = await supervisor.get(f"/api/v1/recommendations/{proposal.recommendation_id}")
    assert after_decision.json()["status_source"].startswith("Approved by Supervisor (KTN) at ")
    assert after_decision.json()["can_apply"] is True

    applied = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["status"] == RecommendationStatus.APPLIED.value
    assert result["order"]["production_state"] == ProductionState.PLANNED.value
    assert sum(Decimal(row["units"]) for row in result["allocations"]) == (
        demo.DEMO_EXPECTED_COVERABLE_UNITS
    )

    async with session_factory() as session:
        allocations = (
            await session.scalars(
                sa.select(Allocation).where(
                    Allocation.order_id == proposal.order_id,
                    Allocation.status == AllocationStatus.ACTIVE.value,
                )
            )
        ).all()
        assert sum((row.units for row in allocations), Decimal(0)) == (
            demo.DEMO_EXPECTED_COVERABLE_UNITS
        )
        assert all(row.recommendation_id == proposal.recommendation_id for row in allocations)

        balance = await _balance(session, proposal.factory_id, demo.DEMO_BOM_MATERIAL_CODE)
        assert balance.reserved - reserved_before == Decimal("1099.98")
        assert balance.version > before_balance.version

        order = await session.get(Order, proposal.order_id)
        assert order is not None
        assert order.production_state == ProductionState.PLANNED.value
        assert order.version > order_version_before

        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.status == RecommendationStatus.APPLIED.value
        assert recommendation.applied_at is not None
        assert recommendation.applied_by == supervisor.user.id

        assert await _actions(session, proposal.recommendation_id) == [
            ("recommendation.decided", "SUCCESS"),
            ("recommendation.applied", "SUCCESS"),
        ]

        refresh = (
            await session.scalars(
                sa.select(Job).where(Job.job_type == "maintenance.refresh_material_states")
            )
        ).all()
        assert [job.dedupe_key for job in refresh] == [
            f"refresh:{proposal.order_id}:{proposal.recommendation_id}"
        ]

        proposer_notifications = (
            await session.scalars(
                sa.select(Notification).where(Notification.user_id == proposal.proposer.user.id)
            )
        ).all()
        assert {row.kind for row in proposer_notifications} == {
            "recommendation.decided",
            "recommendation.applied",
        }

    await supervisor_client.aclose()


async def test_reject_needs_a_reason_and_a_rejected_proposal_cannot_be_applied(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)
    supervisor_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")

    missing_reason = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "REJECTED", "proposal_hash": proposal.proposal_hash},
    )
    assert missing_reason.status_code == 422, missing_reason.text
    assert missing_reason.json()["error"]["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in missing_reason.json()["error"]["field_errors"]] == [
        "reason"
    ]

    rejected = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={
            "decision": "REJECTED",
            "reason": "The line is reserved for a rush order.",
            "proposal_hash": proposal.proposal_hash,
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == RecommendationStatus.REJECTED.value

    applied = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 409, applied.text
    assert applied.json()["error"]["code"] == "CONFLICT"

    async with session_factory() as session:
        assert (
            await session.scalar(
                sa.select(sa.func.count()).select_from(
                    sa.select(Allocation).where(Allocation.order_id == proposal.order_id).subquery()
                )
            )
            == 0
        )
    await supervisor_client.aclose()


async def test_a_changed_hash_and_an_expired_proposal_are_refused(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)
    supervisor_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")

    mismatched = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": "0" * 64},
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["error"]["code"] == "CONFLICT"

    async with session_factory() as session:
        await session.execute(
            sa.update(Recommendation)
            .where(Recommendation.id == proposal.recommendation_id)
            .values(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))
        )
        await session.commit()

    expired = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )
    assert expired.status_code == 409, expired.text
    assert expired.json()["error"]["code"] == "EXPIRED"

    async with session_factory() as session:
        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.status == RecommendationStatus.EXPIRED.value
    await supervisor_client.aclose()


async def test_apply_rejects_stale_inputs_and_supersedes_the_recommendation(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    """Reference fixture 6: stock moved on after the approval."""
    proposal = await propose(db_session, session_factory, settings, client, app)
    supervisor_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")
    storekeeper_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    storekeeper = await login_as(storekeeper_client, session_factory, "storekeeper@demo.test")

    decided = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )
    assert decided.status_code == 200, decided.text

    async with session_factory() as session:
        balance = await _balance(session, proposal.factory_id, demo.DEMO_BOM_MATERIAL_CODE)
        material_id = balance.material_id
        balance_version = balance.version
        lot_id = await session.scalar(
            sa.select(MaterialLot.id)
            .where(
                MaterialLot.factory_id == proposal.factory_id,
                MaterialLot.material_id == material_id,
                MaterialLot.status == MaterialLotStatus.ACCEPTED.value,
            )
            .order_by(MaterialLot.id)
        )
        assert lot_id is not None

    issue = await storekeeper.post(
        f"/api/v1/factories/{proposal.factory_id}/stock/issues",
        json={
            "material_id": str(material_id),
            "lot_id": str(lot_id),
            "quantity": "5",
            "reason": "Sample cutting",
        },
        headers=key("issue"),
    )
    assert issue.status_code == 201, issue.text

    applied = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 409, applied.text
    error = applied.json()["error"]
    assert error["code"] == "STALE_INPUT"
    # The issue moves the balance *and*, through the material-state recompute,
    # the order row; both are reported.
    fields = [item["field"] for item in error["field_errors"]]
    assert "material_balance" in fields

    async with session_factory() as session:
        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.status == RecommendationStatus.SUPERSEDED.value
        assert recommendation.superseded_reason is not None
        assert recommendation.superseded_reason.startswith("STALE_INPUT: ")
        assert "material_balance" in recommendation.superseded_reason

        allocations = (
            await session.scalars(
                sa.select(Allocation).where(Allocation.order_id == proposal.order_id)
            )
        ).all()
        assert allocations == []

        order = await session.get(Order, proposal.order_id)
        assert order is not None
        assert order.production_state == ProductionState.VALIDATED.value

        balance = await _balance(session, proposal.factory_id, demo.DEMO_BOM_MATERIAL_CODE)
        assert balance.version != balance_version

        notified = (
            await session.scalars(
                sa.select(Notification).where(
                    Notification.user_id == proposal.proposer.user.id,
                    Notification.kind == "recommendation.superseded",
                )
            )
        ).all()
        assert len(notified) == 1
        assert "run a new analysis" in notified[0].body

    await supervisor_client.aclose()
    await storekeeper_client.aclose()


async def test_apply_is_idempotent_per_key(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)
    supervisor_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")
    await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )

    headers = key("apply-once")
    first = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    second = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json() == first.json()

    third = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply-twice"),
    )
    assert third.status_code == 409, third.text
    assert third.json()["error"]["code"] == "CONFLICT"

    async with session_factory() as session:
        allocations = (
            await session.scalars(
                sa.select(Allocation).where(
                    Allocation.order_id == proposal.order_id,
                    Allocation.status == AllocationStatus.ACTIVE.value,
                )
            )
        ).all()
        assert len(allocations) == len(first.json()["allocations"])
        reservations = (
            await session.scalars(
                sa.select(Reservation).where(
                    Reservation.order_id == proposal.order_id,
                    Reservation.status == ReservationStatus.ACTIVE.value,
                )
            )
        ).all()
        assert len(reservations) == len(first.json()["reservations"])
    await supervisor_client.aclose()


async def test_apply_rolls_back_entirely_when_one_allocation_no_longer_fits(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    owner_engine: AsyncEngine,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)
    supervisor_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")
    await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )

    async with session_factory() as session:
        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        rows = list(recommendation.proposal["allocations"])
        assert len(rows) >= 2, "this test needs a multi-slot proposal"
        victim = uuid.UUID(str(rows[-1]["slot_id"]))

    # Shrink the last slot's capacity *without* bumping its version, so the
    # staleness check passes and the failure happens mid-apply.
    async with owner_engine.begin() as connection:
        await connection.execute(
            sa.update(LineCapacitySlot)
            .where(LineCapacitySlot.id == victim)
            .values(available_operator_minutes=Decimal("1"))
        )

    applied = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 409, applied.text
    assert applied.json()["error"]["code"] == "CONFLICT"

    async with session_factory() as session:
        assert (
            await session.scalars(
                sa.select(Allocation).where(Allocation.order_id == proposal.order_id)
            )
        ).all() == []
        assert (
            await session.scalars(
                sa.select(Reservation).where(
                    Reservation.order_id == proposal.order_id,
                    Reservation.recommendation_id == proposal.recommendation_id,
                )
            )
        ).all() == []
        order = await session.get(Order, proposal.order_id)
        assert order is not None
        assert order.production_state == ProductionState.VALIDATED.value
        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.status == RecommendationStatus.APPROVED.value
    await supervisor_client.aclose()


async def test_a_second_apply_by_a_different_user_after_success_conflicts(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)
    supervisor_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    supervisor = await login_as(supervisor_client, session_factory, "supervisor@demo.test")
    await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )
    first = await supervisor.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply"),
    )
    assert first.status_code == 200, first.text

    other_client = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    other = await login_as(other_client, session_factory, "supervisor.b@demo.test")
    second = await other.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply-b"),
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "CONFLICT"

    async with session_factory() as session:
        user = await session.get(User, supervisor.user.id)
        assert user is not None
        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.applied_by == user.id
    await supervisor_client.aclose()
    await other_client.aclose()


# --------------------------------------------------------------------------
# Directly built proposals: the gate checks that do not need the agent flow
# --------------------------------------------------------------------------


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _direct(
    db_session: AsyncSession,
    identity: IdentityFixture,
    *,
    input_versions: dict[str, Any] | None = None,
    status: str = RecommendationStatus.APPROVED.value,
) -> tuple[Order, Recommendation, LineCapacitySlot]:
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    slot = await make_slot(db_session, line=line)
    order, recommendation = await build_approved(
        db_session,
        identity,
        factory,
        slot,
        standard_minutes=Decimal(60),
        units=Decimal(10),
        input_versions=input_versions,
        status=status,
    )
    await db_session.commit()
    return order, recommendation, slot


async def _supervisor(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[AsyncClient, Any]:
    own = AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001
    return own, await login_as(own, session_factory, "supervisor@demo.test")


async def test_apply_fails_closed_when_input_versions_omit_a_proposal_slot(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    identity: IdentityFixture,
) -> None:
    """Nothing proves an unpinned slot has not moved, so it counts as stale."""
    order, recommendation, slot = await _direct(
        db_session,
        identity,
        input_versions={"order": {}, "capacity_slots": {}, "material_balances": {}},
    )
    own, supervisor = await _supervisor(client, session_factory)

    applied = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply",
        json={"proposal_hash": recommendation.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 409, applied.text
    error = applied.json()["error"]
    assert error["code"] == "STALE_INPUT"
    assert sorted(item["field"] for item in error["field_errors"]) == ["capacity_slot", "order"]
    assert all("recorded no version for it" in item["message"] for item in error["field_errors"])

    async with session_factory() as session:
        stored = await session.get(Recommendation, recommendation.id)
        assert stored is not None
        assert stored.status == RecommendationStatus.SUPERSEDED.value
        assert (
            await session.scalars(sa.select(Allocation).where(Allocation.order_id == order.id))
        ).all() == []
        fresh_slot = await session.get(LineCapacitySlot, slot.id)
        assert fresh_slot is not None
        assert fresh_slot.allocated_standard_minutes == Decimal(0)
    await own.aclose()


async def test_apply_fails_closed_on_a_malformed_input_version_entry(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    identity: IdentityFixture,
) -> None:
    """A version that is not an integer pins nothing."""
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    slot = await make_slot(db_session, line=line)
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=factory,
        production_state=ProductionState.VALIDATED.value,
    )
    await db_session.flush()
    _, recommendation = await build_approved(
        db_session,
        identity,
        factory,
        slot,
        standard_minutes=Decimal(60),
        units=Decimal(10),
    )
    recommendation.input_versions = {
        "order": {str(recommendation.order_id): recommendation.version},
        "capacity_slots": {str(slot.id): "1"},  # a string, not an int
        "material_balances": {"not-a-uuid": 1},
    }
    sa.orm.attributes.flag_modified(recommendation, "input_versions")
    await db_session.commit()
    own, supervisor = await _supervisor(client, session_factory)

    applied = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply",
        json={"proposal_hash": recommendation.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 409, applied.text
    error = applied.json()["error"]
    assert error["code"] == "STALE_INPUT"
    assert "capacity_slot" in [item["field"] for item in error["field_errors"]]

    async with session_factory() as session:
        stored = await session.get(Recommendation, recommendation.id)
        assert stored is not None
        assert stored.status == RecommendationStatus.SUPERSEDED.value
        assert (
            await session.scalars(sa.select(Allocation).where(Allocation.order_id == order.id))
        ).all() == []
    await own.aclose()


async def test_apply_refuses_a_changed_hash_and_an_expired_proposal(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    identity: IdentityFixture,
) -> None:
    _, recommendation, _ = await _direct(db_session, identity)
    own, supervisor = await _supervisor(client, session_factory)

    mismatched = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply",
        json={"proposal_hash": "0" * 64},
        headers=key("apply"),
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["error"]["code"] == "CONFLICT"
    assert mismatched.json()["error"]["message"] == "Proposal changed"

    async with session_factory() as session:
        await session.execute(
            sa.update(Recommendation)
            .where(Recommendation.id == recommendation.id)
            .values(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))
        )
        await session.commit()

    expired = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply",
        json={"proposal_hash": recommendation.proposal_hash},
        headers=key("apply"),
    )
    assert expired.status_code == 409, expired.text
    assert expired.json()["error"]["code"] == "EXPIRED"

    async with session_factory() as session:
        stored = await session.get(Recommendation, recommendation.id)
        assert stored is not None
        assert stored.status == RecommendationStatus.EXPIRED.value
    await own.aclose()


async def test_an_applied_proposal_cannot_be_decided_again(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    identity: IdentityFixture,
) -> None:
    _, recommendation, _ = await _direct(db_session, identity)
    own, supervisor = await _supervisor(client, session_factory)

    applied = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply",
        json={"proposal_hash": recommendation.proposal_hash},
        headers=key("apply"),
    )
    assert applied.status_code == 200, applied.text

    decided = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/decision",
        json={
            "decision": "REJECTED",
            "reason": "Too late.",
            "proposal_hash": (recommendation.proposal_hash),
        },
    )
    assert decided.status_code == 409, decided.text
    assert decided.json()["error"]["code"] == "CONFLICT"
    assert "APPLIED" in decided.json()["error"]["message"]

    async with session_factory() as session:
        stored = await session.get(Recommendation, recommendation.id)
        assert stored is not None
        assert stored.status == RecommendationStatus.APPLIED.value
        assert (
            await session.scalar(
                sa.select(sa.func.count()).select_from(
                    sa.select(Approval)
                    .where(Approval.recommendation_id == recommendation.id)
                    .subquery()
                )
            )
            == 0
        )
    await own.aclose()


async def test_the_key_of_a_stale_apply_can_be_retried(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    identity: IdentityFixture,
) -> None:
    """`idempotency.release` frees the claim a committed rejection would burn."""
    _, recommendation, _ = await _direct(
        db_session,
        identity,
        input_versions={"order": {}, "capacity_slots": {}, "material_balances": {}},
    )
    own, supervisor = await _supervisor(client, session_factory)
    headers = key("apply-stale")
    body = {"proposal_hash": recommendation.proposal_hash}

    first = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply", json=body, headers=headers
    )
    assert first.status_code == 409, first.text
    assert first.json()["error"]["code"] == "STALE_INPUT"

    async with session_factory() as session:
        assert (
            await session.scalar(
                sa.select(sa.func.count()).select_from(sa.select(IdempotencyKey).subquery())
            )
            == 0
        ), "the claim must not survive a committed rejection"

    # The same key is usable again: the answer is now about the recommendation
    # (SUPERSEDED), never "a request with this Idempotency-Key is in progress".
    second = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/apply", json=body, headers=headers
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "CONFLICT"
    assert "SUPERSEDED" in second.json()["error"]["message"]
    await own.aclose()


@pytest.mark.parametrize(
    ("generated_by", "label"),
    [("model", "AI recommendation"), ("deterministic", "Calculated from records")],
)
async def test_status_source_labels_track_generated_by(
    generated_by: str,
    label: str,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    identity: IdentityFixture,
) -> None:
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    slot = await make_slot(db_session, line=line)
    _, recommendation = await build_approved(
        db_session,
        identity,
        factory,
        slot,
        standard_minutes=Decimal(60),
        units=Decimal(10),
        status=RecommendationStatus.PROPOSED.value,
    )
    recommendation.generated_by = generated_by
    await db_session.commit()
    own, supervisor = await _supervisor(client, session_factory)

    detail = await supervisor.get(f"/api/v1/recommendations/{recommendation.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status_source"] == label

    decided = await supervisor.post(
        f"/api/v1/recommendations/{recommendation.id}/decision",
        json={"decision": "APPROVED", "proposal_hash": recommendation.proposal_hash},
    )
    assert decided.status_code == 200, decided.text
    after = await supervisor.get(f"/api/v1/recommendations/{recommendation.id}")
    assert after.json()["status_source"].startswith("Approved by Supervisor (KTN) at ")
    await own.aclose()
