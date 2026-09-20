"""Worker-crash resilience (task-25-brief.md req. 6): a real subprocess worker,
SIGKILLed mid-task, must never lose or duplicate work.

Two real OS processes do the work here (not the in-process test worker other
suites use): a plain ``uvicorn`` server for the internal dispatch endpoint
(``orchestrator.advance`` posts to it over real HTTP, exactly as in
production), and a real ``python -m app.jobs`` worker. The run is seeded
directly in the database (mirroring what ``POST /orders/{id}/analyses``
does) so the test does not also need a browser-style login/CSRF round trip
against the real server.

Sequence: start the server; seed a QUEUED run (enqueues
``orchestrator.advance``); start worker 1 with a 3-second-per-call fixture
delay (``LS_FIXTURE_DELAY_SECONDS``) and a 5-second lease
(``LS_WORKER_LEASE_SECONDS``); once its first agent task is observably
``RUNNING`` (so it is inside the delayed call, holding the lease), SIGKILL
it; start worker 2 (no delay) and let it reclaim the job once the lease
expires and drive the run to a terminal status.

Assertions: the run reaches a terminal status; the task worker 1 was killed
mid-flight on has *exactly one* stored result (the unique
``(task_id)`` index on ``agent_results`` is what guarantees this, but a
crash at the wrong moment could in principle have produced zero -- this
proves it produced exactly one, not zero and not two); and no
allocation/reservation was created (recommendations here are never
auto-applied, so there is nothing a duplicate delivery could double-write).
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.analyses import start_run
from app.auth.policy import Principal
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    Allocation,
    AnalysisRun,
    Order,
    Reservation,
    User,
)
from app.domain.vocab import TaskStatus
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.security]

BACKEND_DIR = Path(__file__).resolve().parents[2]
TERMINAL_RUN_STATUSES = {"AWAITING_REVIEW", "COMPLETED", "DEGRADED", "FAILED", "CANCELLED"}
POLL_TIMEOUT_SECONDS = 45.0
POLL_INTERVAL_SECONDS = 0.25


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _base_env(settings: Settings, *, port: int, tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "LS_ENVIRONMENT": "test",
            "LS_DATABASE_URL": settings.database_url,
            "LS_SERVICE_TOKEN": settings.service_token.get_secret_value(),
            "LS_SESSION_SECRET": settings.session_secret.get_secret_value(),
            "LS_EMBEDDER": "hashing",
            "LS_LLM_PROVIDER": "fixture",
            "LS_DOCUMENT_STORAGE_DIR": str(tmp_path / "documents"),
            "LS_WORKER_ALIVE_DIR": str(tmp_path / "alive"),
            "LS_API_INTERNAL_URL": f"http://127.0.0.1:{port}",
        }
    )
    return env


def _terminate(process: subprocess.Popen[bytes], *, label: str) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _wait_for_server(port: int) -> None:
    deadline = asyncio.get_event_loop().time() + 15.0
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/api/health/live", timeout=1.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise AssertionError("the internal dispatch server never became ready")


async def _poll_until(
    predicate_sql: sa.Select[tuple[str]],
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeout_seconds: float,
) -> str | None:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            value = await session.scalar(predicate_sql)
        if value is not None:
            return str(value)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return None


async def test_a_sigkilled_worker_never_loses_or_duplicates_a_task(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    tmp_path: Path,
) -> None:
    anchor = datetime.now(ZoneInfo("Asia/Colombo")).date()
    await seed_demo(db_session, anchor_date=anchor, issuer=settings.oidc_issuer)
    order = await db_session.scalar(sa.select(Order).where(Order.external_ref == DEMO_ORDER_REF))
    assert order is not None
    planner_user = await db_session.scalar(sa.select(User).where(User.email == "planner@demo.test"))
    assert planner_user is not None
    principal = Principal(
        user_id=planner_user.id,
        organization_id=order.organization_id,
        session_id=uuid.uuid4(),
        csrf_token="test-csrf",
        display_name=planner_user.display_name,
        roles_by_factory={order.factory_id: frozenset({"planner"})},
    )

    run = await start_run(
        db_session,
        principal,
        order,
        settings=settings,
        trace_id="resilience-test",
        idempotency_key=f"resilience-{uuid.uuid4()}",
    )
    run_id = run.id
    await db_session.commit()

    # `seed_demo` seeds a full realistic dataset (other orders' historical
    # allocations/reservations included), so the invariant under test is "no
    # *new* row appears while this run is processed" -- a before/after delta
    # of zero -- not "the table is empty".
    async def _counts() -> tuple[int, int]:
        allocations = await db_session.scalar(
            sa.select(sa.func.count())
            .select_from(Allocation)
            .where(Allocation.organization_id == order.organization_id)
        )
        reservations = await db_session.scalar(
            sa.select(sa.func.count())
            .select_from(Reservation)
            .where(Reservation.organization_id == order.organization_id)
        )
        return int(allocations or 0), int(reservations or 0)

    before_allocations, before_reservations = await _counts()

    port = _free_port()
    server_env = _base_env(settings, port=port, tmp_path=tmp_path)
    # Real, blocking `subprocess.Popen` (not `asyncio.create_subprocess_exec`)
    # is deliberate: the test needs synchronous `.kill()`/`.wait()`/`.poll()`
    # on a real OS process (to SIGKILL it and observe its exit code), not
    # async I/O streaming.
    server = subprocess.Popen(  # noqa: S603, ASYNC220
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=BACKEND_DIR,
        env=server_env,
    )
    worker1: subprocess.Popen[bytes] | None = None
    worker2: subprocess.Popen[bytes] | None = None
    try:
        await _wait_for_server(port)

        worker1_env = dict(server_env)
        worker1_env["LS_FIXTURE_DELAY_SECONDS"] = "3"
        worker1_env["LS_WORKER_LEASE_SECONDS"] = "5"
        worker1 = subprocess.Popen(  # noqa: S603, ASYNC220
            [
                sys.executable,
                "-m",
                "app.jobs",
                "--concurrency",
                "1",
                "--worker-id",
                "resilience-worker-1",
            ],
            cwd=BACKEND_DIR,
            env=worker1_env,
        )

        rm_task_running = sa.select(AgentTask.id).where(
            AgentTask.run_id == run_id,
            AgentTask.recipient == "rm",
            AgentTask.round == 0,
            AgentTask.status == TaskStatus.RUNNING.value,
        )
        task_id_str = await _poll_until(rm_task_running, session_factory, timeout_seconds=20.0)
        assert task_id_str is not None, "worker 1 never started the rm round-0 task"
        rm_task_id = uuid.UUID(task_id_str)

        # It just transitioned to RUNNING; give it a moment to actually be
        # inside the delayed fixture call (holding the job's lease) before
        # the kill.
        await asyncio.sleep(0.5)
        assert worker1.poll() is None, "worker 1 exited before it could be killed mid-task"
        worker1.kill()
        worker1.wait(timeout=5)
        assert worker1.returncode is not None and worker1.returncode != 0

        worker2_env = dict(server_env)
        worker2_env["LS_WORKER_LEASE_SECONDS"] = "5"
        worker2 = subprocess.Popen(  # noqa: S603, ASYNC220
            [
                sys.executable,
                "-m",
                "app.jobs",
                "--concurrency",
                "1",
                "--worker-id",
                "resilience-worker-2",
            ],
            cwd=BACKEND_DIR,
            env=worker2_env,
        )

        run_terminal = sa.select(AnalysisRun.status).where(
            AnalysisRun.id == run_id, AnalysisRun.status.in_(TERMINAL_RUN_STATUSES)
        )
        final_status = await _poll_until(
            run_terminal, session_factory, timeout_seconds=POLL_TIMEOUT_SECONDS
        )
        assert final_status is not None, "the run never reached a terminal status after recovery"
        assert final_status in TERMINAL_RUN_STATUSES
    finally:
        if worker1 is not None:
            _terminate(worker1, label="worker1")
        if worker2 is not None:
            _terminate(worker2, label="worker2")
        _terminate(server, label="server")

    async with session_factory() as session:
        result_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentResultRecord)
            .where(AgentResultRecord.task_id == rm_task_id)
        )
        assert result_count == 1, "the killed task must have exactly one stored result"

        after_allocations = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Allocation)
            .where(Allocation.organization_id == order.organization_id)
        )
        after_reservations = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Reservation)
            .where(Reservation.organization_id == order.organization_id)
        )
        # Nothing in this flow auto-applies a recommendation (that always
        # needs a human decision), so processing this run -- including the
        # kill-and-resume -- must not have created any new allocation or
        # reservation row (a duplicate delivery of the killed task's job
        # would have been the way that happened).
        assert after_allocations == before_allocations
        assert after_reservations == before_reservations
