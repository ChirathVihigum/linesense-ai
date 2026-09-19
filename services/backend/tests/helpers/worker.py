"""Run the real worker until the queues are idle (integration tests).

``drain`` is the test-side stand-in for ``python -m app.jobs``: the full
handler registry, one job at a time, with the orchestrator's dispatch client
bound to the in-process ASGI app instead of a real HTTP server.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs.handlers import build_registry
from app.jobs.queue import QUEUES
from app.jobs.worker import Worker
from app.orchestration.dispatch import AgentDispatchClient
from app.settings import Settings

MAX_JOBS = 200


def dispatch_client(settings: Settings, transport: httpx.AsyncBaseTransport) -> AgentDispatchClient:
    """A dispatch client that talks to ``transport`` with the service token."""
    return AgentDispatchClient(
        settings.api_internal_url,
        settings.service_token.get_secret_value(),
        transport=transport,
    )


def make_worker(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport,
    worker_id: str | None = None,
) -> Worker:
    return Worker(
        registry=build_registry(settings),
        session_factory=session_factory,
        settings=settings,
        queues=list(QUEUES),
        concurrency=1,
        lease_seconds=30,
        heartbeat_seconds=10,
        poll_interval=0.01,
        worker_id=worker_id or f"test-{uuid.uuid4().hex[:8]}",
        reconcile_seconds=None,
        dispatch_client=dispatch_client(settings, transport),
    )


async def drain(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport,
    max_jobs: int = MAX_JOBS,
    worker_id: str | None = None,
) -> int:
    """Process jobs until nothing is runnable; returns how many ran."""
    worker = make_worker(session_factory, settings, transport=transport, worker_id=worker_id)
    processed = 0
    while processed < max_jobs and await worker.run_once():
        processed += 1
    if processed >= max_jobs:  # pragma: no cover - a runaway loop is a bug, not a pass
        raise AssertionError(f"drain did not settle after {max_jobs} jobs")
    return processed
