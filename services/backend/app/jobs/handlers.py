"""Job handler registry used by ``python -m app.jobs``.

Later tasks register their handlers in :func:`build_registry`.
"""

from __future__ import annotations

import structlog

from app.jobs.reconcile import purge_expired_idempotency_keys, reconcile_in_transaction
from app.jobs.worker import HandlerRegistry, JobContext, finish_in_transaction
from app.settings import Settings

logger = structlog.get_logger("app.jobs")

MAINTENANCE_RECONCILE = "maintenance.reconcile"
MAINTENANCE_PURGE_IDEMPOTENCY = "maintenance.purge_idempotency"


async def handle_reconcile(ctx: JobContext) -> None:
    async with ctx.session_factory() as session, session.begin():
        report = await reconcile_in_transaction(session)
        await finish_in_transaction(ctx, session)
    logger.info(
        "maintenance.reconciled",
        job_id=str(ctx.job.id),
        idempotency_keys_expired=report.idempotency_keys_expired,
        recommendations_expired=report.recommendations_expired,
    )


async def handle_purge_idempotency(ctx: JobContext) -> None:
    async with ctx.session_factory() as session, session.begin():
        purged = await purge_expired_idempotency_keys(session)
        await finish_in_transaction(ctx, session)
    logger.info("maintenance.idempotency_purged", job_id=str(ctx.job.id), purged=purged)


def build_registry(settings: Settings) -> HandlerRegistry:
    """Every job type the worker can run (``settings`` is for handlers added later)."""
    registry = HandlerRegistry()
    registry.register(MAINTENANCE_RECONCILE, handle_reconcile)
    registry.register(MAINTENANCE_PURGE_IDEMPOTENCY, handle_purge_idempotency)
    return registry
