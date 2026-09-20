"""Job handler registry used by ``python -m app.jobs``.

Later tasks register their handlers in :func:`build_registry`.
"""

from __future__ import annotations

import uuid

import structlog

from app.domain.inventory.service import recompute_material_states
from app.jobs.reconcile import purge_expired_idempotency_keys, reconcile_in_transaction
from app.jobs.worker import HandlerRegistry, JobContext, finish_in_transaction
from app.orchestration.executor import (
    AGENT_EXECUTE_JOB,
    ORCHESTRATOR_ADVANCE_JOB,
    execute_agent_task,
    on_agent_task_exhausted,
)
from app.orchestration.orchestrator import advance_run
from app.retrieval.embedder import build_embedder
from app.retrieval.pipeline import (
    DOCUMENT_PROCESS_JOB,
    PROCESSING_FAILED_MESSAGE,
    process_document_version,
    reject_document_version,
)
from app.retrieval.storage import DocumentStorage
from app.settings import Settings, resolve_backend_path

logger = structlog.get_logger("app.jobs")

MAINTENANCE_RECONCILE = "maintenance.reconcile"
MAINTENANCE_PURGE_IDEMPOTENCY = "maintenance.purge_idempotency"
# Kept in sync with app.domain.orders.service.REFRESH_MATERIAL_STATES_JOB
# (the enqueueing side); a mismatch fails a test rather than silently
# leaving cancellation's refresh jobs unhandled.
MAINTENANCE_REFRESH_MATERIAL_STATES = "maintenance.refresh_material_states"


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


async def handle_refresh_material_states(ctx: JobContext) -> None:
    """Recompute `orders.material_state` after an order cancellation released
    reservations, without holding an order lock and a balance lock across
    two different orders in the same transaction (see
    `app.domain.orders.service._release_and_enqueue_refresh`).
    """
    payload = ctx.job.payload
    factory_id = uuid.UUID(payload["factory_id"])
    material_ids = [uuid.UUID(material_id) for material_id in payload["material_ids"]]
    async with ctx.session_factory() as session, session.begin():
        updated = await recompute_material_states(session, factory_id, material_ids)
        await finish_in_transaction(ctx, session)
    logger.info(
        "maintenance.material_states_refreshed",
        job_id=str(ctx.job.id),
        factory_id=str(factory_id),
        material_count=len(material_ids),
        orders_evaluated=len(updated),
    )


def _document_storage(settings: Settings) -> DocumentStorage:
    return DocumentStorage(resolve_backend_path(settings.document_storage_dir))


async def handle_document_process(ctx: JobContext) -> None:
    """Extract, chunk, embed and activate/reject one quarantined document version.

    Unexpected errors are left to propagate: the worker retries the job with
    backoff (backend-contracts.md section 7); only once attempts are
    exhausted does ``on_document_process_exhausted`` reject the version.
    Rejections the pipeline itself detects (unsupported document, extraction
    timeout) are handled inside ``process_document_version`` and never raise.
    """
    version_id = uuid.UUID(str(ctx.job.payload["document_version_id"]))
    embedder = build_embedder(ctx.settings)
    storage = _document_storage(ctx.settings)
    await process_document_version(
        ctx.session_factory, version_id, embedder=embedder, storage=storage
    )
    async with ctx.session_factory() as session, session.begin():
        await finish_in_transaction(ctx, session)
    logger.info("document.processed", job_id=str(ctx.job.id), document_version_id=str(version_id))


async def on_document_process_exhausted(ctx: JobContext, error: str) -> None:
    """Last-chance outcome once every ``document.process`` attempt has failed."""
    version_id = uuid.UUID(str(ctx.job.payload["document_version_id"]))
    storage = _document_storage(ctx.settings)
    await reject_document_version(
        ctx.session_factory, version_id, reason=PROCESSING_FAILED_MESSAGE, storage=storage
    )
    logger.warning(
        "document.processing_exhausted",
        job_id=str(ctx.job.id),
        document_version_id=str(version_id),
        error=error,
    )


def build_registry(settings: Settings) -> HandlerRegistry:
    """Every job type the worker can run (``settings`` is for handlers added later)."""
    registry = HandlerRegistry()
    registry.register(MAINTENANCE_RECONCILE, handle_reconcile)
    registry.register(MAINTENANCE_PURGE_IDEMPOTENCY, handle_purge_idempotency)
    registry.register(MAINTENANCE_REFRESH_MATERIAL_STATES, handle_refresh_material_states)
    registry.register(AGENT_EXECUTE_JOB, execute_agent_task, on_exhausted=on_agent_task_exhausted)
    registry.register(ORCHESTRATOR_ADVANCE_JOB, advance_run)
    registry.register(
        DOCUMENT_PROCESS_JOB, handle_document_process, on_exhausted=on_document_process_exhausted
    )
    return registry
