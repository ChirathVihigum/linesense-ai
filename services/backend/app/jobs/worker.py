"""Worker runtime: handler registry, per-job heartbeats, fencing, graceful stop.

A handler receives a :class:`JobContext`. It performs its business writes and
calls :func:`finish_in_transaction` in the *same* transaction, which raises
:class:`~app.jobs.queue.LeaseLostError` when the lease was lost so that the
transaction rolls back. A handler that returns without completing is
completed by the worker in a fresh (still fenced) transaction.

Outcome mapping:

- handler returns -> ``DONE``
- :class:`~app.jobs.queue.PermanentJobError` or unknown job type -> ``FAILED``
- any other exception -> retry with backoff (``FAILED`` once attempts run out)
- lease lost (heartbeat fenced out or ``LeaseLostError``) -> nothing: the job
  belongs to whichever worker holds the new lease.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job
from app.domain.vocab import JobStatus
from app.jobs.queue import (
    ClaimedJob,
    LeaseLostError,
    PermanentJobError,
    claim,
    complete,
    fail,
    format_error,
    heartbeat,
)
from app.jobs.reconcile import reconcile_once
from app.settings import Settings, resolve_backend_path

logger = structlog.get_logger("app.jobs")

SHUTDOWN_GRACE_SECONDS = 20.0
RECONCILE_INTERVAL_SECONDS = 15.0
ALIVE_LOG_INTERVAL_SECONDS = 30.0
MAX_ERROR_BACKOFF_SECONDS = 5.0


@dataclass
class JobContext:
    job: ClaimedJob
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    worker_id: str


JobHandler = Callable[[JobContext], Awaitable[None]]
ExhaustedHook = Callable[[JobContext, str], Awaitable[None]]


@dataclass(frozen=True)
class RegisteredHandler:
    job_type: str
    handler: JobHandler
    on_exhausted: ExhaustedHook | None = None


class UnknownJobTypeError(PermanentJobError):
    """No handler is registered for the job's type."""


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, RegisteredHandler] = {}

    def register(
        self,
        job_type: str,
        handler: JobHandler,
        *,
        on_exhausted: ExhaustedHook | None = None,
    ) -> None:
        if job_type in self._handlers:
            raise ValueError(f"a handler for job type {job_type!r} is already registered")
        self._handlers[job_type] = RegisteredHandler(job_type, handler, on_exhausted)

    def get(self, job_type: str) -> RegisteredHandler:
        try:
            return self._handlers[job_type]
        except KeyError:
            raise UnknownJobTypeError(f"no handler registered for job type {job_type!r}") from None

    def job_types(self) -> list[str]:
        return sorted(self._handlers)


async def finish_in_transaction(ctx: JobContext, session: AsyncSession) -> None:
    """Complete ``ctx.job`` inside ``session``'s transaction or raise ``LeaseLostError``."""
    if not await complete(session, job_id=ctx.job.id, lease_token=ctx.job.lease_token):
        raise LeaseLostError(f"lease on job {ctx.job.id} was lost")


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        queues: Sequence[str],
        concurrency: int = 4,
        lease_seconds: int = 30,
        heartbeat_seconds: float = 10,
        poll_interval: float = 0.5,
        worker_id: str | None = None,
        reconcile_seconds: float | None = RECONCILE_INTERVAL_SECONDS,
        shutdown_grace_seconds: float = SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if not queues:
            raise ValueError("at least one queue is required")
        if heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        self.registry = registry
        self.session_factory = session_factory
        self.settings = settings
        self.queues = list(queues)
        self.concurrency = concurrency
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_interval = poll_interval
        self.worker_id = worker_id or _default_worker_id()
        self.reconcile_seconds = reconcile_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.active_jobs = 0
        self._last_alive_log = 0.0

    # ------------------------------------------------------------------ liveness

    @property
    def alive_path(self) -> Path | None:
        if self.settings.environment == "production":
            return None
        directory = resolve_backend_path(self.settings.worker_alive_dir)
        return directory / f"worker-{self.worker_id}.alive"

    def _touch_alive(self) -> None:
        path = self.alive_path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        now = time.monotonic()
        if now - self._last_alive_log >= ALIVE_LOG_INTERVAL_SECONDS:
            self._last_alive_log = now
            logger.info(
                "worker.alive",
                worker_id=self.worker_id,
                queues=self.queues,
                active_jobs=self.active_jobs,
            )

    # ---------------------------------------------------------------- processing

    def _log_fields(self, job: ClaimedJob) -> dict[str, object]:
        return {
            "job_id": str(job.id),
            "job_type": job.job_type,
            "queue": job.queue,
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "worker_id": self.worker_id,
        }

    def _context(self, job: ClaimedJob) -> JobContext:
        return JobContext(
            job=job,
            session_factory=self.session_factory,
            settings=self.settings,
            worker_id=self.worker_id,
        )

    async def _on_exhausted(self, job: ClaimedJob, error: str) -> None:
        try:
            entry = self.registry.get(job.job_type)
        except UnknownJobTypeError:
            return
        if entry.on_exhausted is not None:
            await entry.on_exhausted(self._context(job), error)

    async def run_once(self) -> bool:
        """Claim and fully process at most one job; ``False`` when nothing was runnable."""
        job = await claim(
            self.session_factory,
            queues=self.queues,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            on_exhausted=self._on_exhausted,
        )
        if job is None:
            return False
        self.active_jobs += 1
        try:
            await self._process(job)
        finally:
            self.active_jobs -= 1
        return True

    async def _heartbeat_loop(
        self, job: ClaimedJob, handler_task: asyncio.Task[None], lease_lost: asyncio.Event
    ) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                extended = await heartbeat(
                    self.session_factory,
                    job_id=job.id,
                    lease_token=job.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                # Transient database trouble: keep trying; if it persists the
                # lease expires and the job is recovered by another claim.
                logger.exception("job.heartbeat_error", **self._log_fields(job))
                continue
            if not extended:
                lease_lost.set()
                handler_task.cancel()
                return

    async def _fail(self, job: ClaimedJob, exc: BaseException, *, retryable: bool) -> None:
        error = format_error(exc)
        logger.warning(
            "job.failed",
            error=error,
            retryable=retryable,
            will_retry=retryable and job.attempt < job.max_attempts,
            **self._log_fields(job),
        )
        await fail(
            self.session_factory,
            job_id=job.id,
            lease_token=job.lease_token,
            error=error,
            retryable=retryable,
            on_exhausted=self._on_exhausted,
        )

    async def _complete_after_handler(self, job: ClaimedJob) -> bool:
        """Complete in a fresh transaction unless the handler already did."""
        async with self.session_factory() as session, session.begin():
            if await complete(session, job_id=job.id, lease_token=job.lease_token):
                return True
            status = await session.scalar(
                sa.select(Job.status).where(Job.id == job.id, Job.lease_token == job.lease_token)
            )
        return status == JobStatus.DONE.value

    async def _process(self, job: ClaimedJob) -> None:
        fields = self._log_fields(job)
        logger.info("job.claimed", **fields)
        try:
            entry = self.registry.get(job.job_type)
        except UnknownJobTypeError as exc:
            await self._fail(job, exc, retryable=False)
            return

        lease_lost = asyncio.Event()
        context = self._context(job)

        async def run_handler() -> None:
            await entry.handler(context)

        handler_task = asyncio.create_task(run_handler())
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job, handler_task, lease_lost))
        try:
            try:
                await handler_task
            finally:
                # Stop heartbeating before recording the outcome.
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if lease_lost.is_set() and current is not None and not current.cancelling():
                logger.warning("job.lease_lost", reason="heartbeat fenced out", **fields)
                return
            raise
        except LeaseLostError:
            logger.warning("job.lease_lost", reason="complete fenced out", **fields)
            return
        except PermanentJobError as exc:
            await self._fail(job, exc, retryable=False)
            return
        except Exception as exc:
            await self._fail(job, exc, retryable=True)
            return

        if await self._complete_after_handler(job):
            logger.info("job.completed", **fields)
        else:
            logger.warning("job.lease_lost", reason="complete fenced out", **fields)

    # ------------------------------------------------------------------- running

    async def _slot(self, stop_event: asyncio.Event) -> None:
        consecutive_errors = 0
        while not stop_event.is_set():
            self._touch_alive()
            try:
                processed = await self.run_once()
            except Exception:
                consecutive_errors += 1
                logger.exception(
                    "worker.loop_error",
                    worker_id=self.worker_id,
                    consecutive_errors=consecutive_errors,
                )
                processed = False
            else:
                consecutive_errors = 0
            if not processed:
                # Back off while the database is unavailable instead of
                # logging a traceback on every poll.
                delay = min(self.poll_interval * 2**consecutive_errors, MAX_ERROR_BACKOFF_SECONDS)
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)

    async def _reconcile_loop(self, stop_event: asyncio.Event, interval: float) -> None:
        while not stop_event.is_set():
            try:
                report = await reconcile_once(self.session_factory, self.settings)
            except Exception:
                logger.exception("worker.reconcile_error", worker_id=self.worker_id)
            else:
                if report.idempotency_keys_expired or report.recommendations_expired:
                    logger.info(
                        "worker.reconciled",
                        worker_id=self.worker_id,
                        idempotency_keys_expired=report.idempotency_keys_expired,
                        recommendations_expired=report.recommendations_expired,
                    )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Process jobs until ``stop_event`` is set, then shut down gracefully.

        After the stop signal no new jobs are claimed; running handlers get up
        to ``shutdown_grace_seconds`` to finish. Handlers still running after
        that are cancelled; their jobs are recovered by lease expiry.
        """
        logger.info(
            "worker.started",
            worker_id=self.worker_id,
            queues=self.queues,
            concurrency=self.concurrency,
        )
        slots = [
            asyncio.create_task(self._slot(stop_event), name=f"job-slot-{n}")
            for n in range(self.concurrency)
        ]
        background: list[asyncio.Task[None]] = []
        if self.reconcile_seconds is not None:
            background.append(
                asyncio.create_task(self._reconcile_loop(stop_event, self.reconcile_seconds))
            )
        try:
            await stop_event.wait()
            logger.info("worker.stopping", worker_id=self.worker_id, active_jobs=self.active_jobs)
            _, pending = await asyncio.wait(slots, timeout=self.shutdown_grace_seconds)
            if pending:
                logger.warning(
                    "worker.shutdown_timeout",
                    worker_id=self.worker_id,
                    unfinished_slots=len(pending),
                )
        finally:
            for task in (*slots, *background):
                task.cancel()
            await asyncio.gather(*slots, *background, return_exceptions=True)
            path = self.alive_path
            if path is not None:
                path.unlink(missing_ok=True)
            logger.info("worker.stopped", worker_id=self.worker_id)
