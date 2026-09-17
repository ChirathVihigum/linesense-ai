"""Worker runtime and reconciliation against the real PostgreSQL test database."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.db.models import AuditEvent, IdempotencyKey, Job, Recommendation, RunEvent
from app.jobs.handlers import build_registry
from app.jobs.queue import PermanentJobError, RetryableJobError, enqueue
from app.jobs.reconcile import reconcile_once
from app.jobs.worker import HandlerRegistry, JobContext, Worker, finish_in_transaction
from app.settings import Settings
from tests.factories import make_org, make_recommendation, make_run, utcnow

pytestmark = pytest.mark.integration


def _worker(
    registry: HandlerRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    **kwargs: Any,
) -> Worker:
    params: dict[str, Any] = {
        "registry": registry,
        "session_factory": session_factory,
        "settings": settings,
        "queues": ["maintenance"],
        "concurrency": 1,
        "lease_seconds": 30,
        "heartbeat_seconds": 10,
        "poll_interval": 0.05,
        "worker_id": f"test-{uuid.uuid4().hex[:8]}",
        "reconcile_seconds": None,
    }
    params.update(kwargs)
    return Worker(**params)


async def _enqueue(
    session_factory: async_sessionmaker[AsyncSession], job_type: str, **kwargs: Any
) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        job_id = await enqueue(
            session,
            queue="maintenance",
            job_type=job_type,
            payload=kwargs.pop("payload", {}),
            **kwargs,
        )
    assert job_id is not None
    return job_id


async def _job(session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> Job:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def _new_run_id(session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        run = await make_run(session)
        return run.id


async def test_run_once_is_false_when_idle(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    assert await _worker(HandlerRegistry(), session_factory, settings).run_once() is False


async def test_run_once_processes_job_end_to_end(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    run_id = await _new_run_id(session_factory)
    seen: list[dict[str, Any]] = []

    async def handler(ctx: JobContext) -> None:
        seen.append(ctx.job.payload)
        async with ctx.session_factory() as session, session.begin():
            session.add(
                RunEvent(run_id=run_id, event_type="job.write", actor=ctx.worker_id, payload={})
            )
            await finish_in_transaction(ctx, session)

    registry = HandlerRegistry()
    registry.register("test.write", handler)
    job_id = await _enqueue(session_factory, "test.write", payload={"x": 1})
    worker = _worker(registry, session_factory, settings)

    with capture_logs() as logs:
        assert await worker.run_once() is True
    assert seen == [{"x": 1}]
    row = await _job(session_factory, job_id)
    assert row.status == "DONE"
    assert row.attempt == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(RunEvent)) == 1
    events = [entry["event"] for entry in logs]
    assert events == ["job.claimed", "job.completed"]
    assert logs[1]["job_id"] == str(job_id)
    assert logs[1]["job_type"] == "test.write"
    assert logs[1]["attempt"] == 1


async def test_handler_that_does_not_complete_is_completed_by_worker(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async def handler(ctx: JobContext) -> None:
        return None

    registry = HandlerRegistry()
    registry.register("test.noop", handler)
    job_id = await _enqueue(session_factory, "test.noop")

    assert await _worker(registry, session_factory, settings).run_once() is True
    row = await _job(session_factory, job_id)
    assert row.status == "DONE"
    assert row.completed_at is not None


async def test_permanent_error_fails_job_and_runs_hook(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    hook_calls: list[tuple[uuid.UUID, str]] = []

    async def handler(ctx: JobContext) -> None:
        raise PermanentJobError("bad input")

    async def on_exhausted(ctx: JobContext, error: str) -> None:
        hook_calls.append((ctx.job.id, error))

    registry = HandlerRegistry()
    registry.register("test.permanent", handler, on_exhausted=on_exhausted)
    job_id = await _enqueue(session_factory, "test.permanent")

    with capture_logs() as logs:
        assert await _worker(registry, session_factory, settings).run_once() is True
    row = await _job(session_factory, job_id)
    assert row.status == "FAILED"
    assert row.attempt == 1
    assert row.last_error == "'PermanentJobError': bad input"
    assert hook_calls == [(job_id, "'PermanentJobError': bad input")]
    failed = [entry for entry in logs if entry["event"] == "job.failed"]
    assert len(failed) == 1
    assert failed[0]["retryable"] is False


async def test_unknown_job_type_fails_permanently(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    job_id = await _enqueue(session_factory, "test.nobody-handles-this")

    assert await _worker(HandlerRegistry(), session_factory, settings).run_once() is True
    row = await _job(session_factory, job_id)
    assert row.status == "FAILED"
    assert row.attempt == 1
    assert row.last_error is not None
    assert "UnknownJobTypeError" in row.last_error
    assert "test.nobody-handles-this" in row.last_error


@pytest.mark.parametrize("error", [RuntimeError("flaky"), RetryableJobError("flaky")])
async def test_other_errors_are_retried_with_backoff(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    db_session: AsyncSession,
    error: Exception,
) -> None:
    run_id = await _new_run_id(session_factory)

    async def handler(ctx: JobContext) -> None:
        async with ctx.session_factory() as session, session.begin():
            session.add(RunEvent(run_id=run_id, event_type="partial", actor="w", payload={}))
            await session.flush()
            raise error

    registry = HandlerRegistry()
    registry.register("test.flaky", handler)
    job_id = await _enqueue(session_factory, "test.flaky")

    assert await _worker(registry, session_factory, settings).run_once() is True
    row = await _job(session_factory, job_id)
    db_now = await db_session.scalar(select(func.now()))
    assert db_now is not None
    assert row.status == "READY"
    assert row.attempt == 1
    assert row.last_error == f"{type(error).__name__!r}: flaky"
    assert row.available_at - db_now > timedelta(seconds=3)
    async with session_factory() as session:
        # The handler's transaction rolled back with the error.
        assert await session.scalar(select(func.count()).select_from(RunEvent)) == 0


async def test_lost_heartbeat_cancels_handler_without_failing_job(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    registry = HandlerRegistry()
    registry.register("test.slow", handler)
    job_id = await _enqueue(session_factory, "test.slow")
    worker = _worker(registry, session_factory, settings, lease_seconds=5, heartbeat_seconds=0.1)

    with capture_logs() as logs:
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), timeout=5)
        # Another worker takes over the lease (simulated): our token is now stale.
        new_token = uuid.uuid4()
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_token = :token, worker_id = 'other' WHERE id = :id"),
                {"token": new_token, "id": job_id},
            )
        assert await asyncio.wait_for(task, timeout=5) is True

    assert cancelled.is_set()
    row = await _job(session_factory, job_id)
    assert row.status == "LEASED"
    assert row.lease_token == new_token
    assert row.worker_id == "other"
    assert row.last_error is None
    events = [entry["event"] for entry in logs]
    assert "job.lease_lost" in events
    assert "job.failed" not in events
    assert "job.completed" not in events


async def test_fenced_complete_rolls_back_handler_writes(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    run_id = await _new_run_id(session_factory)

    async def handler(ctx: JobContext) -> None:
        # Our lease expires and another worker reclaims the job meanwhile.
        async with ctx.session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE jobs SET lease_token = :token WHERE id = :id"),
                {"token": uuid.uuid4(), "id": ctx.job.id},
            )
        async with ctx.session_factory() as session, session.begin():
            session.add(RunEvent(run_id=run_id, event_type="stale", actor="w", payload={}))
            await finish_in_transaction(ctx, session)

    registry = HandlerRegistry()
    registry.register("test.fenced", handler)
    job_id = await _enqueue(session_factory, "test.fenced")

    with capture_logs() as logs:
        assert await _worker(registry, session_factory, settings).run_once() is True
    row = await _job(session_factory, job_id)
    assert row.status == "LEASED"
    assert row.last_error is None
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(RunEvent)) == 0
    events = [entry["event"] for entry in logs]
    assert "job.lease_lost" in events
    assert "job.failed" not in events


async def test_graceful_stop_waits_for_running_handler(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    started = asyncio.Event()
    handled: list[uuid.UUID] = []

    async def handler(ctx: JobContext) -> None:
        started.set()
        await asyncio.sleep(0.5)
        handled.append(ctx.job.id)

    registry = HandlerRegistry()
    registry.register("test.slow", handler)
    job_id = await _enqueue(session_factory, "test.slow")
    worker = _worker(registry, session_factory, settings, concurrency=2)
    alive_path = worker.alive_path
    assert alive_path is not None

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(worker.run(stop_event))
    await asyncio.wait_for(started.wait(), timeout=5)
    assert alive_path.exists()
    assert alive_path.parent.name == ".local"
    stop_event.set()
    # A job enqueued after the stop signal is not claimed.
    late_id = await _enqueue(session_factory, "test.slow")
    await asyncio.wait_for(run_task, timeout=5)

    assert handled == [job_id]
    assert (await _job(session_factory, job_id)).status == "DONE"
    assert (await _job(session_factory, late_id)).status == "READY"
    assert not alive_path.exists()


async def test_stop_after_grace_period_leaves_job_for_lease_recovery(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    started = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        started.set()
        await asyncio.sleep(30)

    registry = HandlerRegistry()
    registry.register("test.stuck", handler)
    job_id = await _enqueue(session_factory, "test.stuck")
    worker = _worker(registry, session_factory, settings, shutdown_grace_seconds=0.2)

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(worker.run(stop_event))
    await asyncio.wait_for(started.wait(), timeout=5)
    with capture_logs() as logs:
        stop_event.set()
        await asyncio.wait_for(run_task, timeout=5)

    row = await _job(session_factory, job_id)
    assert row.status == "LEASED"
    assert row.last_error is None
    assert "worker.shutdown_timeout" in [entry["event"] for entry in logs]


async def test_worker_runs_concurrent_jobs(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    running = 0
    peak = 0
    done = asyncio.Event()
    handled: list[uuid.UUID] = []

    async def handler(ctx: JobContext) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.2)
        running -= 1
        handled.append(ctx.job.id)
        if len(handled) == 4:
            done.set()

    registry = HandlerRegistry()
    registry.register("test.parallel", handler)
    job_ids = {await _enqueue(session_factory, "test.parallel") for _ in range(4)}
    worker = _worker(registry, session_factory, settings, concurrency=4)

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(worker.run(stop_event))
    await asyncio.wait_for(done.wait(), timeout=5)
    stop_event.set()
    await asyncio.wait_for(run_task, timeout=5)

    assert set(handled) == job_ids
    assert peak > 1
    async with session_factory() as session:
        statuses = (await session.scalars(select(Job.status))).all()
    assert statuses == ["DONE"] * 4


async def test_registry_rejects_duplicates_and_lists_maintenance_jobs(settings: Settings) -> None:
    registry = build_registry(settings)
    assert registry.job_types() == ["maintenance.purge_idempotency", "maintenance.reconcile"]

    async def handler(ctx: JobContext) -> None:
        return None

    with pytest.raises(ValueError, match="already registered"):
        registry.register("maintenance.reconcile", handler)


async def _expired_fixtures(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, uuid.UUID]:
    past = utcnow() - timedelta(minutes=5)
    future = utcnow() + timedelta(hours=1)
    async with session_factory() as session, session.begin():
        org = await make_org(session)
        run = await make_run(session)
        ids = {
            "proposed_expired": (await make_recommendation(session, run=run, expires_at=past)).id,
            "approved_expired": (
                await make_recommendation(session, run=run, status="APPROVED", expires_at=past)
            ).id,
            "applied_expired": (
                await make_recommendation(session, run=run, status="APPLIED", expires_at=past)
            ).id,
            "proposed_fresh": (await make_recommendation(session, run=run, expires_at=future)).id,
        }
        for name, expires_at in (("key_expired", past), ("key_fresh", future)):
            key = IdempotencyKey(
                organization_id=org.id,
                actor_id="user-1",
                operation="order:create",
                key=name,
                request_hash="h",
                expires_at=expires_at,
            )
            session.add(key)
            await session.flush()
            ids[name] = key.id
        ids["run"] = run.id
    return ids


async def _statuses(session_factory: async_sessionmaker[AsyncSession]) -> dict[uuid.UUID, str]:
    async with session_factory() as session:
        rows = (await session.execute(select(Recommendation.id, Recommendation.status))).all()
    return {row.id: row.status for row in rows}


async def test_reconcile_once_expires_recommendations_and_idempotency_keys(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    ids = await _expired_fixtures(session_factory)

    report = await reconcile_once(session_factory, settings)
    assert report.idempotency_keys_expired == 1
    assert report.recommendations_expired == 2

    statuses = await _statuses(session_factory)
    assert statuses[ids["proposed_expired"]] == "EXPIRED"
    assert statuses[ids["approved_expired"]] == "EXPIRED"
    assert statuses[ids["applied_expired"]] == "APPLIED"
    assert statuses[ids["proposed_fresh"]] == "PROPOSED"

    async with session_factory() as session:
        keys = (await session.scalars(select(IdempotencyKey.id))).all()
        audits = (await session.scalars(select(AuditEvent).order_by(AuditEvent.target_id))).all()
        expired = await session.get(Recommendation, ids["proposed_expired"])
    assert keys == [ids["key_fresh"]]
    assert expired is not None and expired.version == 2
    assert sorted(a.target_id for a in audits) == sorted(
        [str(ids["proposed_expired"]), str(ids["approved_expired"])]
    )
    for audit in audits:
        assert audit.actor_type == "SYSTEM"
        assert audit.action == "recommendation.expire"
        assert audit.outcome == "SUCCESS"
        assert audit.run_id == ids["run"]
        assert audit.after == {"status": "EXPIRED", "version": 2}
    assert {a.before["status"] for a in audits if a.before} == {"PROPOSED", "APPROVED"}

    # Idempotent: a second round changes nothing and audits nothing.
    again = await reconcile_once(session_factory, settings)
    assert again.idempotency_keys_expired == 0
    assert again.recommendations_expired == 0
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 2


async def test_reconcile_once_honours_explicit_now(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    ids = await _expired_fixtures(session_factory)

    report = await reconcile_once(session_factory, settings, now=utcnow() - timedelta(hours=1))
    assert report.recommendations_expired == 0
    assert report.idempotency_keys_expired == 0

    report = await reconcile_once(session_factory, settings, now=utcnow() + timedelta(hours=2))
    assert report.recommendations_expired == 3
    assert report.idempotency_keys_expired == 2
    assert (await _statuses(session_factory))[ids["proposed_fresh"]] == "EXPIRED"


async def test_maintenance_reconcile_job_runs_through_worker(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    ids = await _expired_fixtures(session_factory)
    reconcile_id = await _enqueue(session_factory, "maintenance.reconcile")
    purge_id = await _enqueue(session_factory, "maintenance.purge_idempotency")
    worker = _worker(build_registry(settings), session_factory, settings)

    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert (await _job(session_factory, reconcile_id)).status == "DONE"
    assert (await _job(session_factory, purge_id)).status == "DONE"
    assert (await _statuses(session_factory))[ids["proposed_expired"]] == "EXPIRED"
    async with session_factory() as session:
        keys = (await session.scalars(select(IdempotencyKey.id))).all()
    assert keys == [ids["key_fresh"]]


async def test_loop_errors_back_off_instead_of_spinning(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    worker = _worker(HandlerRegistry(), session_factory, settings, poll_interval=0.05)
    calls = 0

    async def broken_run_once() -> bool:
        nonlocal calls
        calls += 1
        raise RuntimeError("database unavailable")

    worker.run_once = broken_run_once  # type: ignore[method-assign]
    stop_event = asyncio.Event()
    with capture_logs() as logs:
        run_task = asyncio.create_task(worker.run(stop_event))
        await asyncio.sleep(0.6)
        stop_event.set()
        await asyncio.wait_for(run_task, timeout=5)

    # Without backoff a 0.05 s poll would retry about 12 times; with doubling
    # delays (0.1, 0.2, 0.4, ...) there are at most 4 attempts in 0.6 s.
    assert 2 <= calls <= 4
    errors = [entry for entry in logs if entry["event"] == "worker.loop_error"]
    assert [entry["consecutive_errors"] for entry in errors] == list(range(1, calls + 1))
