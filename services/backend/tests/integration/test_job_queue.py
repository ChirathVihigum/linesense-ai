"""Durable job queue primitives against the real PostgreSQL test database."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, RunEvent
from app.jobs.queue import (
    MAX_ERROR_CHARS,
    ClaimedJob,
    backoff_seconds,
    claim,
    complete,
    enqueue,
    fail,
    format_error,
    heartbeat,
)
from tests.factories import make_run

pytestmark = pytest.mark.integration

WORKER_A = "worker-a"
WORKER_B = "worker-b"


async def _enqueue(
    session_factory: async_sessionmaker[AsyncSession], **kwargs: object
) -> uuid.UUID:
    params: dict[str, object] = {"queue": "maintenance", "job_type": "test.job", "payload": {}}
    params.update(kwargs)
    async with session_factory() as session, session.begin():
        job_id = await enqueue(session, **params)  # type: ignore[arg-type]
    assert job_id is not None
    return job_id


async def _job(session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> Job:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def _expire_lease(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE jobs SET leased_until = now() - interval '1 second' WHERE id = :id"),
            {"id": job_id},
        )


async def _claim(
    session_factory: async_sessionmaker[AsyncSession], worker_id: str = WORKER_A, **kwargs: object
) -> ClaimedJob | None:
    return await claim(
        session_factory,
        queues=["maintenance"],
        worker_id=worker_id,
        lease_seconds=30,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_enqueue_with_same_dedupe_key_creates_one_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        first = await enqueue(
            session, queue="maintenance", job_type="t", payload={"n": 1}, dedupe_key="k-1"
        )
        second = await enqueue(
            session, queue="maintenance", job_type="t", payload={"n": 2}, dedupe_key="k-1"
        )
    assert first is not None
    assert second is None

    async with session_factory() as session:
        rows = (await session.scalars(select(Job))).all()
    assert len(rows) == 1
    assert rows[0].id == first
    assert rows[0].payload == {"n": 1}
    assert rows[0].status == "READY"
    assert rows[0].attempt == 0


async def test_enqueue_rejects_unknown_queue(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="unknown queue"):
        await enqueue(db_session, queue="nope", job_type="t", payload={})


async def test_concurrent_claimers_claim_every_job_exactly_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_ids = {await _enqueue(session_factory, payload={"i": i}) for i in range(20)}

    async def claimer(worker_id: str) -> list[uuid.UUID]:
        claimed: list[uuid.UUID] = []
        while (job := await _claim(session_factory, worker_id)) is not None:
            claimed.append(job.id)
            await asyncio.sleep(0)
        return claimed

    results = await asyncio.gather(*(claimer(f"worker-{n}") for n in range(4)))
    all_claimed = [job_id for result in results for job_id in result]

    assert len(all_claimed) == 20
    assert set(all_claimed) == job_ids
    async with session_factory() as session:
        statuses = (await session.execute(select(Job.status, Job.attempt))).all()
    assert all(status == "LEASED" and attempt == 1 for status, attempt in statuses)


async def test_future_available_at_is_not_claimed(
    session_factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    db_now = await db_session.scalar(select(func.now()))
    assert db_now is not None
    await _enqueue(session_factory, available_at=db_now + timedelta(hours=1))

    assert await _claim(session_factory) is None


async def test_claim_orders_by_available_at_and_returns_lease(
    session_factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    db_now = await db_session.scalar(select(func.now()))
    assert db_now is not None
    later = await _enqueue(session_factory, available_at=db_now - timedelta(seconds=1))
    earlier = await _enqueue(session_factory, available_at=db_now - timedelta(seconds=10))

    job = await _claim(session_factory)
    assert job is not None
    assert job.id == earlier
    assert job.attempt == 1
    assert job.max_attempts == 3
    assert job.queue == "maintenance"
    assert job.job_type == "test.job"

    row = await _job(session_factory, earlier)
    assert row.status == "LEASED"
    assert row.lease_token == job.lease_token
    assert row.worker_id == WORKER_A
    assert row.leased_until is not None
    assert row.heartbeat_at is not None
    assert timedelta(seconds=29) <= row.leased_until - row.heartbeat_at <= timedelta(seconds=31)

    second = await _claim(session_factory)
    assert second is not None and second.id == later


async def test_claim_only_takes_requested_queues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _enqueue(session_factory, queue="document")
    assert await _claim(session_factory) is None
    job = await claim(
        session_factory, queues=["agent", "document"], worker_id=WORKER_A, lease_seconds=30
    )
    assert job is not None and job.queue == "document"


async def test_fencing_rolls_back_stale_worker_and_new_owner_completes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        run = await make_run(session)
        run_id = run.id
    job_id = await _enqueue(session_factory)

    job_a = await _claim(session_factory, WORKER_A)
    assert job_a is not None and job_a.id == job_id
    await _expire_lease(session_factory, job_id)

    job_b = await _claim(session_factory, WORKER_B)
    assert job_b is not None and job_b.id == job_id
    assert job_b.attempt == 2
    assert job_b.lease_token != job_a.lease_token

    # Worker A: business write and complete in one transaction; fenced out -> roll back.
    async with session_factory() as session:
        session.add(RunEvent(run_id=run_id, event_type="stale.write", actor="a", payload={}))
        await session.flush()
        completed_a = await complete(session, job_id=job_id, lease_token=job_a.lease_token)
        assert completed_a is False
        await session.rollback()

    async with session_factory() as session, session.begin():
        session.add(RunEvent(run_id=run_id, event_type="fresh.write", actor="b", payload={}))
        await session.flush()
        assert await complete(session, job_id=job_id, lease_token=job_b.lease_token) is True

    async with session_factory() as session:
        events = (await session.scalars(select(RunEvent.event_type))).all()
    assert events == ["fresh.write"]
    row = await _job(session_factory, job_id)
    assert row.status == "DONE"
    assert row.completed_at is not None
    assert row.worker_id == WORKER_B

    # A completed job can be neither completed again nor reclaimed.
    async with session_factory() as session, session.begin():
        assert await complete(session, job_id=job_id, lease_token=job_b.lease_token) is False
    assert await _claim(session_factory) is None


async def test_heartbeat_extends_lease_only_for_current_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _enqueue(session_factory)
    job = await _claim(session_factory)
    assert job is not None
    before = (await _job(session_factory, job_id)).leased_until
    assert before is not None

    assert (
        await heartbeat(session_factory, job_id=job_id, lease_token=uuid.uuid4(), lease_seconds=300)
        is False
    )
    assert (await _job(session_factory, job_id)).leased_until == before

    assert (
        await heartbeat(
            session_factory, job_id=job_id, lease_token=job.lease_token, lease_seconds=300
        )
        is True
    )
    after = (await _job(session_factory, job_id)).leased_until
    assert after is not None and after - before > timedelta(seconds=200)

    # Once reclaimed by another worker, the old token is stale.
    await _expire_lease(session_factory, job_id)
    reclaimed = await _claim(session_factory, WORKER_B)
    assert reclaimed is not None
    assert (
        await heartbeat(
            session_factory, job_id=job_id, lease_token=job.lease_token, lease_seconds=30
        )
        is False
    )


async def test_retryable_failure_backs_off_then_exhausts_once(
    session_factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    job_id = await _enqueue(session_factory, max_attempts=2)
    exhausted: list[str] = []

    async def on_exhausted(exhausted_job: ClaimedJob, error: str) -> None:
        assert exhausted_job.id == job_id
        assert exhausted_job.attempt == 2
        exhausted.append(error)

    job = await _claim(session_factory)
    assert job is not None and job.attempt == 1
    assert await fail(
        session_factory,
        job_id=job_id,
        lease_token=job.lease_token,
        error="boom 1",
        retryable=True,
        on_exhausted=on_exhausted,
    )
    row = await _job(session_factory, job_id)
    db_now = await db_session.scalar(select(func.now()))
    assert db_now is not None
    assert row.status == "READY"
    assert row.attempt == 1
    assert row.last_error == "boom 1"
    assert row.lease_token is None
    assert row.leased_until is None
    # attempt 1 -> min(2 ** 1 * 2, 60) = 4 s plus < 1 s jitter.
    delay = (row.available_at - db_now).total_seconds()
    assert 3.0 <= delay <= 5.5
    assert exhausted == []
    assert await _claim(session_factory) is None  # still backing off

    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE jobs SET available_at = now() WHERE id = :id"), {"id": job_id}
        )
    job = await _claim(session_factory)
    assert job is not None and job.attempt == 2
    assert await fail(
        session_factory,
        job_id=job_id,
        lease_token=job.lease_token,
        error="boom 2",
        retryable=True,
        on_exhausted=on_exhausted,
    )
    row = await _job(session_factory, job_id)
    assert row.status == "FAILED"
    assert row.attempt == 2
    assert row.last_error == "boom 2"
    assert row.completed_at is not None
    assert exhausted == ["boom 2"]

    # A second (stale) fail call is fenced out and does not re-run the hook.
    assert (
        await fail(
            session_factory,
            job_id=job_id,
            lease_token=job.lease_token,
            error="late",
            retryable=False,
            on_exhausted=on_exhausted,
        )
        is False
    )
    assert exhausted == ["boom 2"]
    assert (await _job(session_factory, job_id)).last_error == "boom 2"


async def test_non_retryable_failure_is_terminal_and_truncated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _enqueue(session_factory)
    job = await _claim(session_factory)
    assert job is not None
    assert await fail(
        session_factory,
        job_id=job_id,
        lease_token=job.lease_token,
        error="x" * 5000,
        retryable=False,
    )
    row = await _job(session_factory, job_id)
    assert row.status == "FAILED"
    assert row.attempt == 1
    assert row.last_error is not None
    assert len(row.last_error) == MAX_ERROR_CHARS


async def test_lease_expired_job_at_max_attempts_is_failed_by_next_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _enqueue(session_factory, max_attempts=1)
    later_id = await _enqueue(session_factory)
    async with session_factory() as session, session.begin():
        # Keep the second job behind the first in claim order.
        await session.execute(
            text("UPDATE jobs SET available_at = now() + interval '1 hour' WHERE id = :id"),
            {"id": later_id},
        )
    job = await _claim(session_factory)
    assert job is not None and job.attempt == 1
    await _expire_lease(session_factory, job_id)

    hook_calls: list[tuple[uuid.UUID, str]] = []

    async def on_exhausted(exhausted_job: ClaimedJob, error: str) -> None:
        hook_calls.append((exhausted_job.id, error))

    assert await _claim(session_factory, WORKER_B, on_exhausted=on_exhausted) is None
    assert hook_calls == [(job_id, "attempts exhausted after lease expiry")]
    row = await _job(session_factory, job_id)
    assert row.status == "FAILED"
    assert row.attempt == 1
    assert row.last_error == "attempts exhausted after lease expiry"
    assert row.lease_token is None
    assert row.completed_at is not None

    # The next claim does not see it again.
    assert await _claim(session_factory, WORKER_B, on_exhausted=on_exhausted) is None
    assert len(hook_calls) == 1


async def test_exhausted_job_is_skipped_and_next_job_claimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    exhausted_id = await _enqueue(session_factory, max_attempts=1)
    first = await _claim(session_factory)
    assert first is not None and first.id == exhausted_id
    await _expire_lease(session_factory, exhausted_id)
    ready_id = await _enqueue(session_factory)

    job = await _claim(session_factory, WORKER_B)
    assert job is not None and job.id == ready_id
    assert (await _job(session_factory, exhausted_id)).status == "FAILED"


def test_backoff_and_error_formatting() -> None:
    assert 4.0 <= backoff_seconds(1) < 5.0
    assert 8.0 <= backoff_seconds(2) < 9.0
    assert 60.0 <= backoff_seconds(10) < 61.0

    formatted = format_error(ValueError("y" * 900))
    assert formatted == "'ValueError': " + "y" * 500


async def test_claim_skips_rows_locked_by_another_transaction(
    session_factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    db_now = await db_session.scalar(select(func.now()))
    assert db_now is not None
    first = await _enqueue(session_factory, available_at=db_now - timedelta(seconds=10))
    second = await _enqueue(session_factory, available_at=db_now - timedelta(seconds=1))

    async with session_factory() as locker, locker.begin():
        locked = await locker.scalar(select(Job.id).where(Job.id == first).with_for_update())
        assert locked == first
        # Must not block on the locked row: it is skipped.
        job = await asyncio.wait_for(_claim(session_factory), timeout=2)
        assert job is not None and job.id == second
        assert await asyncio.wait_for(_claim(session_factory), timeout=2) is None

    job = await _claim(session_factory)
    assert job is not None and job.id == first


async def test_format_error_hides_sql_and_parameters(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _enqueue(session_factory, dedupe_key="secret-dedupe-value")
    with pytest.raises(IntegrityError) as caught:
        async with session_factory() as session, session.begin():
            session.add(
                Job(
                    queue="maintenance",
                    job_type="t",
                    payload={},
                    status="READY",
                    dedupe_key="secret-dedupe-value",
                )
            )
            await session.flush()
    formatted = format_error(caught.value)
    assert formatted.startswith("'UniqueViolation': ")
    assert "INSERT" not in formatted
    assert "secret-dedupe-value" not in formatted
