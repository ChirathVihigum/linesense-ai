"""Durable PostgreSQL job queue (backend-contracts.md section 7).

Delivery is at least once. A claim leases a job for ``lease_seconds`` under a
fresh ``lease_token``; every later state change (heartbeat, complete, fail)
is *fenced* by that token and by ``status = 'LEASED'``, so a worker whose
lease expired and was reclaimed by another worker can no longer change the
job. ``complete`` runs inside the caller's transaction, so a fenced-out
worker's business writes roll back together with its completion attempt.

All lease arithmetic uses the database clock (``now()``), never Python time.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import ReturningUpdate

from app.db.models import Job
from app.domain.vocab import JobStatus

logger = structlog.get_logger("app.jobs")

QUEUES: tuple[str, ...] = ("orchestrator", "agent", "document", "maintenance")
MAX_ERROR_CHARS = 2000
MAX_BACKOFF_SECONDS = 60
EXHAUSTED_AFTER_LEASE_EXPIRY = "attempts exhausted after lease expiry"


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    queue: str
    job_type: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    lease_token: uuid.UUID


class LeaseLostError(Exception):
    """The caller's lease token no longer owns the job (fenced out)."""


class RetryableJobError(Exception):
    """A handler asks for a retry with backoff (while attempts remain)."""


class PermanentJobError(Exception):
    """A handler asks for terminal failure without further attempts."""


ExhaustedCallback = Callable[[ClaimedJob, str], Awaitable[None]]


def backoff_seconds(attempt: int) -> float:
    """``min(2 ** attempt * 2, 60)`` seconds plus up to 1 second of jitter."""
    exponent = max(0, min(attempt, 10))  # 2 ** 10 * 2 is already past the cap
    base = min((1 << exponent) * 2, MAX_BACKOFF_SECONDS)
    return base + random.random()  # noqa: S311 (scheduling jitter, not security)


def format_error(exc: BaseException) -> str:
    """Short, secret-free description of ``exc`` (type name plus 500 chars of text)."""
    return repr(type(exc).__name__) + ": " + str(exc)[:500]


def _interval(seconds: float) -> sa.BindParameter[timedelta]:
    return sa.literal(timedelta(seconds=seconds), sa.Interval())


async def enqueue(
    session: AsyncSession,
    *,
    queue: str,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    available_at: datetime | None = None,
    max_attempts: int = 3,
) -> uuid.UUID | None:
    """Insert a READY job in the caller's transaction.

    Returns the new job id, or ``None`` when a job with ``dedupe_key``
    already exists (``INSERT ... ON CONFLICT DO NOTHING``).
    """
    if queue not in QUEUES:
        raise ValueError(f"unknown queue {queue!r}; expected one of {', '.join(QUEUES)}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "queue": queue,
        "job_type": job_type,
        "payload": payload,
        "dedupe_key": dedupe_key,
        "status": JobStatus.READY.value,
        "max_attempts": max_attempts,
    }
    if available_at is not None:
        values["available_at"] = available_at
    statement = (
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
        .returning(Job.id)
    )
    job_id: uuid.UUID | None = await session.scalar(statement)
    return job_id


def _claim_statement(
    queues: Sequence[str], worker_id: str, lease_seconds: int
) -> ReturningUpdate[Any]:
    now = sa.func.now()
    candidate = (
        sa.select(Job.id)
        .where(
            Job.queue.in_(list(queues)),
            sa.or_(
                sa.and_(Job.status == JobStatus.READY.value, Job.available_at <= now),
                sa.and_(Job.status == JobStatus.LEASED.value, Job.leased_until < now),
            ),
        )
        .order_by(Job.available_at, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return (
        sa.update(Job)
        .where(Job.id == candidate)
        .values(
            status=JobStatus.LEASED.value,
            lease_token=uuid.uuid4(),
            worker_id=worker_id,
            attempt=Job.attempt + 1,
            leased_until=now + _interval(lease_seconds),
            heartbeat_at=now,
        )
        .returning(
            Job.id,
            Job.queue,
            Job.job_type,
            Job.payload,
            Job.attempt,
            Job.max_attempts,
            Job.lease_token,
        )
    )


async def _run_exhausted(
    on_exhausted: ExhaustedCallback | None, job: ClaimedJob, error: str
) -> None:
    if on_exhausted is None:
        return
    try:
        await on_exhausted(job, error)
    except Exception:
        # The job is already FAILED and committed; a broken hook must not
        # take the worker down, but it must be visible.
        logger.exception(
            "job.exhausted_hook_failed",
            job_id=str(job.id),
            job_type=job.job_type,
            attempt=job.attempt,
        )


async def claim(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    queues: Sequence[str],
    worker_id: str,
    lease_seconds: int,
    on_exhausted: ExhaustedCallback | None = None,
) -> ClaimedJob | None:
    """Lease the next runnable job in ``queues`` or return ``None``.

    Runnable means ``READY`` with ``available_at <= now()`` or ``LEASED``
    with an expired lease. A reclaimed job whose attempt would exceed
    ``max_attempts`` is marked ``FAILED`` (``on_exhausted`` runs after the
    commit) and the claim is retried.
    """
    if not queues:
        raise ValueError("at least one queue is required")
    statement = _claim_statement(queues, worker_id, lease_seconds)
    while True:
        async with session_factory() as session, session.begin():
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                return None
            job = ClaimedJob(
                id=row.id,
                queue=row.queue,
                job_type=row.job_type,
                payload=dict(row.payload),
                attempt=row.attempt,
                max_attempts=row.max_attempts,
                lease_token=row.lease_token,
            )
            if job.attempt <= job.max_attempts:
                return job
            await session.execute(
                sa.update(Job)
                .where(Job.id == job.id, Job.lease_token == job.lease_token)
                .values(
                    status=JobStatus.FAILED.value,
                    attempt=Job.max_attempts,
                    lease_token=None,
                    leased_until=None,
                    last_error=EXHAUSTED_AFTER_LEASE_EXPIRY,
                    completed_at=sa.func.now(),
                )
            )
        exhausted = ClaimedJob(
            id=job.id,
            queue=job.queue,
            job_type=job.job_type,
            payload=job.payload,
            attempt=job.max_attempts,
            max_attempts=job.max_attempts,
            lease_token=job.lease_token,
        )
        logger.warning(
            "job.failed",
            job_id=str(job.id),
            job_type=job.job_type,
            attempt=exhausted.attempt,
            retryable=False,
            reason=EXHAUSTED_AFTER_LEASE_EXPIRY,
        )
        await _run_exhausted(on_exhausted, exhausted, EXHAUSTED_AFTER_LEASE_EXPIRY)
        statement = _claim_statement(queues, worker_id, lease_seconds)


def _fence(job_id: uuid.UUID, lease_token: uuid.UUID) -> tuple[sa.ColumnElement[bool], ...]:
    return (
        Job.id == job_id,
        Job.lease_token == lease_token,
        Job.status == JobStatus.LEASED.value,
    )


async def heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    lease_seconds: int,
) -> bool:
    """Extend the lease; ``False`` means the lease is lost (stale token or not LEASED)."""
    now = sa.func.now()
    async with session_factory() as session, session.begin():
        extended = await session.scalar(
            sa.update(Job)
            .where(*_fence(job_id, lease_token))
            .values(leased_until=now + _interval(lease_seconds), heartbeat_at=now)
            .returning(Job.id)
        )
    return extended is not None


async def complete(session: AsyncSession, *, job_id: uuid.UUID, lease_token: uuid.UUID) -> bool:
    """Mark the job DONE inside the caller's transaction.

    ``False`` means the caller was fenced out and must roll back its
    transaction. The lease token is kept on the DONE row as a record of which
    lease completed it.
    """
    done = await session.scalar(
        sa.update(Job)
        .where(*_fence(job_id, lease_token))
        .values(
            status=JobStatus.DONE.value,
            leased_until=None,
            completed_at=sa.func.now(),
        )
        .returning(Job.id)
    )
    return done is not None


async def fail(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    error: str,
    retryable: bool,
    on_exhausted: ExhaustedCallback | None = None,
) -> None:
    """Record a failed attempt (fenced; a stale token changes nothing).

    Retryable failures with attempts remaining go back to ``READY`` after
    the backoff; everything else becomes ``FAILED`` and ``on_exhausted``
    runs after the commit.
    """
    error = error[:MAX_ERROR_CHARS]
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            sa.select(Job).where(*_fence(job_id, lease_token)).with_for_update()
        )
        if row is None:
            logger.warning("job.lease_lost", job_id=str(job_id), operation="fail")
            return
        job = ClaimedJob(
            id=row.id,
            queue=row.queue,
            job_type=row.job_type,
            payload=dict(row.payload),
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            lease_token=lease_token,
        )
        will_retry = retryable and job.attempt < job.max_attempts
        common: dict[str, Any] = {
            "lease_token": None,
            "leased_until": None,
            "last_error": error,
        }
        if will_retry:
            values = {
                **common,
                "status": JobStatus.READY.value,
                "available_at": sa.func.now() + _interval(backoff_seconds(job.attempt)),
            }
        else:
            values = {**common, "status": JobStatus.FAILED.value, "completed_at": sa.func.now()}
        await session.execute(sa.update(Job).where(Job.id == job_id).values(**values))

    if not will_retry:
        await _run_exhausted(on_exhausted, job, error)
