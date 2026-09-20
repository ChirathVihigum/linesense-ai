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

Assertions: the run reaches a terminal status; *every* task the run created
(not just the one worker 1 was killed on) has exactly one stored result;
exactly one recommendation was proposed for the run (the kill-and-recover
cycle did not cause the orchestrator to double-propose); driving that
recommendation through a real approve + apply produces exactly one set of
allocations/reservations for the order, and applying the same
(now-APPLIED) recommendation a second time is rejected (409) and creates
no more rows -- so "no duplicate allocations" is a proven outcome of an
actual apply, not a vacuous check on a step nothing ever reached
(task-25 review round 1, item 8).
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
from app.api.errors import AppError
from app.auth.policy import Principal
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    Allocation,
    AnalysisRun,
    Order,
    Recommendation,
    Reservation,
    User,
)
from app.domain.approvals import service as approvals
from app.domain.vocab import RecommendationStatus, TaskStatus
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


async def _order_scoped_count(session: AsyncSession, model: type, order_id: uuid.UUID) -> int:
    count = await session.scalar(
        sa.select(sa.func.count()).select_from(model).where(model.order_id == order_id)
    )
    return int(count or 0)


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

        # Every task the run created -- not just the one worker 1 was
        # killed on -- must have exactly one stored result: a task-25
        # review point (a kill affecting only one task would be a weak
        # proof if sibling/later tasks were never checked at all).
        task_ids = (
            await session.scalars(sa.select(AgentTask.id).where(AgentTask.run_id == run_id))
        ).all()
        assert len(task_ids) >= 4, "expected at least the round-0 rm/ie/quality + planning tasks"
        result_counts_by_task = dict(
            (
                await session.execute(
                    sa.select(AgentResultRecord.task_id, sa.func.count())
                    .where(AgentResultRecord.task_id.in_(task_ids))
                    .group_by(AgentResultRecord.task_id)
                )
            ).all()
        )
        for task_id in task_ids:
            assert result_counts_by_task.get(task_id) == 1, (
                f"task {task_id} has {result_counts_by_task.get(task_id, 0)} results, expected 1"
            )

        # Exactly one recommendation was proposed for this run (the
        # kill-and-recover cycle did not cause the orchestrator to
        # double-propose).
        recommendation_ids = (
            await session.scalars(
                sa.select(Recommendation.id).where(Recommendation.run_id == run_id)
            )
        ).all()
        assert len(recommendation_ids) == 1, (
            f"expected exactly one recommendation for the run, found {len(recommendation_ids)}"
        )
        rec_id = recommendation_ids[0]

        supervisor_user = await session.scalar(
            sa.select(User).where(User.email == "supervisor@demo.test")
        )
        assert supervisor_user is not None
        supervisor_principal = Principal(
            user_id=supervisor_user.id,
            organization_id=order.organization_id,
            session_id=uuid.uuid4(),
            csrf_token="test-csrf",
            display_name=supervisor_user.display_name,
            roles_by_factory={order.factory_id: frozenset({"supervisor"})},
        )

        order_allocations_before_apply = await _order_scoped_count(session, Allocation, order.id)
        order_reservations_before_apply = await _order_scoped_count(session, Reservation, order.id)

    # `decide`/`apply` each run in "the caller's transaction" (their own
    # docstrings): a fresh session + explicit commit per call, exactly as
    # the owning API route does.
    async with session_factory() as session:
        rec = await session.get(Recommendation, rec_id)
        assert rec is not None
        await approvals.decide(
            session,
            supervisor_principal,
            rec_id,
            decision="APPROVED",
            reason=None,
            proposal_hash=rec.proposal_hash,
        )
        await session.commit()

    async with session_factory() as session:
        rec = await session.get(Recommendation, rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.APPROVED.value
        await approvals.apply(
            session, supervisor_principal, rec_id, proposal_hash=rec.proposal_hash
        )
        await session.commit()

    async with session_factory() as session:
        rec = await session.get(Recommendation, rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.APPLIED.value

        order_allocations_after_apply = await _order_scoped_count(session, Allocation, order.id)
        order_reservations_after_apply = await _order_scoped_count(session, Reservation, order.id)
        # The real apply produced *some* allocation and/or reservation for
        # this order (proving apply is not a no-op) -- exactly one set,
        # from exactly one apply. Which of the two depends on
        # `rec.kind` (ALLOCATION / RESERVATION / ALLOCATION_AND_RESERVATION),
        # so the invariant checked here is "at least one side moved",
        # not "both did".
        assert (order_allocations_after_apply - order_allocations_before_apply) + (
            order_reservations_after_apply - order_reservations_before_apply
        ) > 0, "apply() produced no allocation and no reservation for this order"

    # Applying the same, now-APPLIED recommendation again must be rejected
    # (409 CONFLICT, since only an APPROVED recommendation can be applied)
    # and must create no further rows -- the direct proof that nothing
    # here can double-write.
    async with session_factory() as session:
        rec = await session.get(Recommendation, rec_id)
        assert rec is not None
        with pytest.raises(AppError) as exc_info:
            await approvals.apply(
                session, supervisor_principal, rec_id, proposal_hash=rec.proposal_hash
            )
        assert exc_info.value.status_code == 409
        await session.rollback()

    async with session_factory() as session:
        order_allocations_final = await _order_scoped_count(session, Allocation, order.id)
        order_reservations_final = await _order_scoped_count(session, Reservation, order.id)
        assert order_allocations_final == order_allocations_after_apply
        assert order_reservations_final == order_reservations_after_apply

        # Organization-wide totals moved by exactly what this one apply
        # produced for this order -- nothing else wrote to these tables.
        org_allocations_final = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Allocation)
            .where(Allocation.organization_id == order.organization_id)
        )
        org_reservations_final = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Reservation)
            .where(Reservation.organization_id == order.organization_id)
        )
        assert (org_allocations_final or 0) - before_allocations == (
            order_allocations_after_apply - order_allocations_before_apply
        )
        assert (org_reservations_final or 0) - before_reservations == (
            order_reservations_after_apply - order_reservations_before_apply
        )
