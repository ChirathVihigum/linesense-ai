"""Atomic per-run LLM call/token budgets against ``analysis_runs``.

Each function opens its own transaction via ``session_factory`` (following
the fenced-update pattern in ``app/jobs/queue.py``): a single ``UPDATE ...
WHERE ... RETURNING`` is the unit of atomicity, so concurrent reservations
against the same run serialize on Postgres's row lock and each re-check the
predicate against the latest committed row before deciding to update it.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AnalysisRun
from app.domain.vocab import RunStatus

_RESERVABLE_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)


async def reserve_model_call(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> bool:
    """Atomically increment ``model_calls_used`` if the run still has budget.

    Returns ``True`` iff a row was updated: ``model_calls_used <
    model_calls_limit``, ``tokens_used < token_budget``, and the run is
    ``QUEUED`` or ``RUNNING``.
    """
    async with session_factory() as session, session.begin():
        updated = await session.scalar(
            sa.update(AnalysisRun)
            .where(
                AnalysisRun.id == run_id,
                AnalysisRun.model_calls_used < AnalysisRun.model_calls_limit,
                AnalysisRun.tokens_used < AnalysisRun.token_budget,
                AnalysisRun.status.in_(_RESERVABLE_STATUSES),
            )
            .values(model_calls_used=AnalysisRun.model_calls_used + 1)
            .returning(AnalysisRun.model_calls_used)
        )
    return updated is not None


async def record_usage(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Atomically add ``input_tokens + output_tokens`` to ``tokens_used``."""
    total = input_tokens + output_tokens
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(tokens_used=AnalysisRun.tokens_used + total)
        )


async def token_budget_remaining(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> int:
    """Remaining tokens (``token_budget - tokens_used``, floored at 0)."""
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.select(AnalysisRun.token_budget, AnalysisRun.tokens_used).where(
                    AnalysisRun.id == run_id
                )
            )
        ).one()
    token_budget: int = row.token_budget
    tokens_used: int = row.tokens_used
    return max(0, token_budget - tokens_used)
