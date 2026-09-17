"""Atomic run budgets against the real PostgreSQL test database."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AnalysisRun
from app.domain.vocab import RunStatus
from app.llm.budget import record_usage, reserve_model_call, token_budget_remaining
from tests.factories import make_run

pytestmark = pytest.mark.integration


async def _run_id(
    session_factory: async_sessionmaker[AsyncSession], **overrides: object
) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        run = await make_run(session, **overrides)
        return run.id


async def _get(session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> AnalysisRun:
    async with session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        assert run is not None
        return run


async def test_reserve_model_call_stops_exactly_at_the_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, model_calls_limit=12)

    results = [await reserve_model_call(session_factory, run_id) for _ in range(13)]

    assert results == [True] * 12 + [False]
    run = await _get(session_factory, run_id)
    assert run.model_calls_used == 12


async def test_concurrent_reservations_grant_exactly_the_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, model_calls_limit=12)

    results = await asyncio.gather(
        *(reserve_model_call(session_factory, run_id) for _ in range(30))
    )

    assert sum(1 for granted in results if granted) == 12
    run = await _get(session_factory, run_id)
    assert run.model_calls_used == 12


async def test_reserve_model_call_false_once_token_budget_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, model_calls_limit=12, token_budget=1000, tokens_used=0)

    await record_usage(session_factory, run_id, input_tokens=600, output_tokens=400)
    assert await reserve_model_call(session_factory, run_id) is False

    run = await _get(session_factory, run_id)
    assert run.model_calls_used == 0
    assert run.tokens_used == 1000


async def test_reserve_model_call_false_for_a_cancelled_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, status=RunStatus.CANCELLED.value)

    assert await reserve_model_call(session_factory, run_id) is False


async def test_reserve_model_call_true_while_queued_or_running(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    queued_id = await _run_id(session_factory, status=RunStatus.QUEUED.value)
    running_id = await _run_id(session_factory, status=RunStatus.RUNNING.value)

    assert await reserve_model_call(session_factory, queued_id) is True
    assert await reserve_model_call(session_factory, running_id) is True


async def test_record_usage_accumulates_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, token_budget=1000)

    await asyncio.gather(
        *(
            record_usage(session_factory, run_id, input_tokens=10, output_tokens=5)
            for _ in range(20)
        )
    )

    run = await _get(session_factory, run_id)
    assert run.tokens_used == 20 * 15


async def test_token_budget_remaining(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, token_budget=1000)
    assert await token_budget_remaining(session_factory, run_id) == 1000

    await record_usage(session_factory, run_id, input_tokens=300, output_tokens=200)
    assert await token_budget_remaining(session_factory, run_id) == 500

    await record_usage(session_factory, run_id, input_tokens=10_000, output_tokens=0)
    assert await token_budget_remaining(session_factory, run_id) == 0


async def test_reserve_and_record_usage_reject_unknown_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unknown_id = uuid.uuid4()
    assert await reserve_model_call(session_factory, unknown_id) is False
    # record_usage on an unknown run affects no rows; must not raise.
    await record_usage(session_factory, unknown_id, input_tokens=1, output_tokens=1)


async def test_direct_query_matches_helper_after_reservations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_id(session_factory, model_calls_limit=3)
    for _ in range(3):
        assert await reserve_model_call(session_factory, run_id) is True
    assert await reserve_model_call(session_factory, run_id) is False

    async with session_factory() as session:
        used = await session.scalar(
            select(AnalysisRun.model_calls_used).where(AnalysisRun.id == run_id)
        )
    assert used == 3
