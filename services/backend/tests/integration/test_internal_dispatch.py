"""The internal dispatch API: token, envelope validation, idempotency, events."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AgentTask, AuditEvent, Job, RunEvent
from app.domain.vocab import AuditOutcome, RunStatus, TaskStatus
from app.orchestration.dispatch import AgentDispatchClient, DispatchError
from app.orchestration.executor import AGENT_EXECUTE_JOB
from app.orchestration.protocol import InputRef, TaskEnvelope
from app.settings import Settings
from tests.helpers.agents import envelope_for, make_run_with_snapshot

pytestmark = pytest.mark.integration

PATH = "/internal/v1/agent-tasks"


def _auth(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.service_token.get_secret_value()}"}


async def _post(client: AsyncClient, payload: dict[str, Any], settings: Settings) -> Any:
    return await client.post(PATH, json=payload, headers=_auth(settings))


async def test_missing_and_wrong_tokens_are_unauthenticated(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    payload = envelope_for(run, snapshot)

    anonymous = await client.post(PATH, json=payload)
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "UNAUTHENTICATED"

    wrong = await client.post(
        PATH, json=payload, headers={"Authorization": "Bearer not-the-service-token"}
    )
    assert wrong.status_code == 401

    # A non-ASCII token byte must be rejected, not crash the constant-time
    # comparison (`secrets.compare_digest` refuses non-ASCII `str`).
    non_ascii = await client.post(PATH, json=payload, headers={b"Authorization": b"Bearer \xff"})
    assert non_ascii.status_code == 401
    assert non_ascii.json()["error"]["code"] == "UNAUTHENTICATED"
    assert await db_session.scalar(select(func.count()).select_from(AgentTask)) == 0


async def test_valid_envelope_creates_task_job_and_event(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()

    response = await _post(client, envelope_for(run, snapshot), settings)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run_id"] == str(run.id)
    assert body["status"] == TaskStatus.PENDING.value

    task = await db_session.get(AgentTask, uuid.UUID(body["task_id"]))
    assert task is not None
    assert task.recipient == "rm"
    assert task.status == TaskStatus.PENDING.value
    assert task.organization_id == run.organization_id
    assert TaskEnvelope.model_validate(task.envelope).run_id == run.id

    job = await db_session.scalar(select(Job).where(Job.dedupe_key == f"task:{task.id}"))
    assert job is not None
    assert (job.queue, job.job_type, job.max_attempts) == ("agent", AGENT_EXECUTE_JOB, 3)
    assert job.payload == {"task_id": str(task.id)}

    event = await db_session.scalar(
        select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.event_type == "task.dispatched")
    )
    assert event is not None
    assert event.actor == "orchestrator"
    assert event.payload["task_id"] == str(task.id)
    assert event.payload["message"]["idempotency_key"] == task.idempotency_key


async def test_duplicate_dispatch_returns_the_same_task_and_one_job(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    payload = envelope_for(run, snapshot)

    first = await _post(client, payload, settings)
    # A retry re-sends the same envelope with a fresh message id.
    payload["message_id"] = str(uuid.uuid4())
    second = await _post(client, payload, settings)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["task_id"] == first.json()["task_id"]
    assert await db_session.scalar(select(func.count()).select_from(AgentTask)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.job_type == AGENT_EXECUTE_JOB)
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.event_type == "task.dispatched")
        )
        == 1
    )


async def test_foreign_organization_is_rejected_and_audited(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    payload = envelope_for(run, snapshot, organization_id=str(uuid.uuid4()))

    response = await _post(client, payload, settings)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await db_session.scalar(select(func.count()).select_from(AgentTask)) == 0

    audit = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "agent_task.dispatch")
    )
    assert audit is not None
    assert audit.outcome == AuditOutcome.DENIED.value
    assert audit.actor_type == "SERVICE"
    assert audit.run_id == run.id


async def test_task_type_must_match_the_recipient(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    payload = envelope_for(run, snapshot)
    payload["task_type"] = "propose_allocation"  # a planning task type

    response = await _post(client, payload, settings)
    assert response.status_code == 422
    assert await db_session.scalar(select(func.count()).select_from(AgentTask)) == 0


async def test_parent_task_from_another_run_is_rejected(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    other_run, other_snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    created = await _post(client, envelope_for(other_run, other_snapshot), settings)
    foreign_task_id = uuid.UUID(created.json()["task_id"])

    response = await _post(
        client,
        envelope_for(
            run,
            snapshot,
            recipient="planning",
            task_type="propose_allocation",
            parent_task_id=foreign_task_id,
        ),
        settings,
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith("Referenced tasks")

    foreign_ref = await _post(
        client,
        envelope_for(
            run,
            snapshot,
            input_refs=[
                InputRef(type="snapshot", id=snapshot.id),
                InputRef(type="agent_result", id=foreign_task_id),
            ],
        ),
        settings,
    )
    assert foreign_ref.status_code == 422


async def test_wrong_idempotency_key_format_is_rejected(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    payload = envelope_for(run, snapshot, idempotency_key=f"{run.id}:rm:round-0")

    response = await _post(client, payload, settings)
    assert response.status_code == 422
    assert response.json()["error"]["field_errors"][0]["field"] == "idempotency_key"


async def test_cancelled_run_rejects_new_tasks(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session, status=RunStatus.CANCELLED.value)
    await db_session.commit()

    response = await _post(client, envelope_for(run, snapshot), settings)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_deadline_may_not_exceed_the_run_deadline(
    client: AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    from datetime import timedelta

    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    payload = envelope_for(run, snapshot)
    payload["deadline_at"] = (run.deadline_at + timedelta(seconds=60)).isoformat()

    response = await _post(client, payload, settings)
    assert response.status_code == 422
    assert response.json()["error"]["field_errors"][0]["field"] == "deadline_at"

    # A naive timestamp cannot be compared with the run's timestamptz deadline,
    # so the protocol refuses it up front instead of failing later.
    naive = envelope_for(run, snapshot)
    naive["deadline_at"] = run.deadline_at.replace(tzinfo=None).isoformat()
    naive_response = await _post(client, naive, settings)
    assert naive_response.status_code == 422
    assert naive_response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert any(
        error["field"].endswith("deadline_at")
        for error in naive_response.json()["error"]["field_errors"]
    )


async def test_dispatch_client_submits_and_reads_back(
    app: Any,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, snapshot = await make_run_with_snapshot(db_session)
    await db_session.commit()
    dispatch = AgentDispatchClient(
        "http://testserver",
        settings.service_token.get_secret_value(),
        transport=ASGITransport(app=app),
    )
    envelope = TaskEnvelope.model_validate(envelope_for(run, snapshot))

    receipt = await dispatch.submit(envelope)
    assert receipt.run_id == run.id
    assert receipt.status == TaskStatus.PENDING.value

    fetched = await dispatch.get(receipt.task_id)
    assert fetched["task"]["id"] == str(receipt.task_id)
    assert fetched["result"] is None

    rejected = AgentDispatchClient(
        "http://testserver", "wrong-token", transport=ASGITransport(app=app)
    )
    with pytest.raises(DispatchError) as exc_info:
        await rejected.submit(envelope)
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 401
