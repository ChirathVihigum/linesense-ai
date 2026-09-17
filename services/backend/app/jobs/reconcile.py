"""Periodic reconciliation of time-based state (initial scope; Task 13 extends it).

- ``idempotency_keys`` expired for more than an hour are deleted.
- ``recommendations`` still ``PROPOSED``/``APPROVED`` past ``expires_at``
  become ``EXPIRED`` with a ``SYSTEM`` audit event per row.

Every step is idempotent, so several workers may reconcile concurrently:
recommendation rows are locked with ``SKIP LOCKED`` (a row being decided
right now is picked up by a later round) and only rows actually changed are
audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_audit
from app.db.models import IdempotencyKey, Recommendation
from app.domain.vocab import ActorType, AuditOutcome, RecommendationStatus
from app.settings import Settings

RECONCILER_ACTOR_ID = "reconciler"
RECOMMENDATION_BATCH_SIZE = 500
PURGE_BATCH_SIZE = 500
PURGE_GRACE = timedelta(hours=1)
_EXPIRABLE = (RecommendationStatus.PROPOSED.value, RecommendationStatus.APPROVED.value)


@dataclass(frozen=True)
class ReconcileReport:
    idempotency_keys_expired: int
    recommendations_expired: int


def _cutoff(now: datetime | None) -> sa.ColumnElement[datetime]:
    if now is None:
        return sa.func.now()
    return sa.literal(now, sa.TIMESTAMP(timezone=True))


async def purge_expired_idempotency_keys(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete idempotency keys expired for more than ``PURGE_GRACE`` (caller's transaction).

    The grace period absorbs clock skew between API hosts (which stamp
    ``expires_at`` with their own clock) and the database. Rows locked by an
    in-flight ``idempotency.begin`` are skipped and purged by a later round.
    """
    doomed = (
        sa.select(IdempotencyKey.id)
        .where(IdempotencyKey.expires_at < _cutoff(now) - PURGE_GRACE)
        .limit(PURGE_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    deleted = await session.scalars(
        sa.delete(IdempotencyKey)
        .where(IdempotencyKey.id.in_(doomed))
        .returning(IdempotencyKey.id)
        .execution_options(synchronize_session=False)
    )
    return len(deleted.all())


async def expire_recommendations(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Mark overdue PROPOSED/APPROVED recommendations EXPIRED and audit each one."""
    rows = (
        await session.execute(
            sa.select(
                Recommendation.id,
                Recommendation.organization_id,
                Recommendation.factory_id,
                Recommendation.run_id,
                Recommendation.status,
                Recommendation.version,
            )
            .where(
                Recommendation.status.in_(_EXPIRABLE),
                Recommendation.expires_at <= _cutoff(now),
            )
            .order_by(Recommendation.id)
            .limit(RECOMMENDATION_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not rows:
        return 0

    await session.execute(
        sa.update(Recommendation)
        .where(Recommendation.id.in_([row.id for row in rows]))
        .values(status=RecommendationStatus.EXPIRED.value, version=Recommendation.version + 1)
        .execution_options(synchronize_session=False)
    )
    for row in rows:
        await record_audit(
            session,
            organization_id=row.organization_id,
            factory_id=row.factory_id,
            actor_type=ActorType.SYSTEM.value,
            actor_id=RECONCILER_ACTOR_ID,
            action="recommendation.expire",
            target_type="recommendation",
            target_id=str(row.id),
            outcome=AuditOutcome.SUCCESS.value,
            reason="expires_at passed",
            run_id=row.run_id,
            before={"status": row.status, "version": row.version},
            after={"status": RecommendationStatus.EXPIRED.value, "version": row.version + 1},
        )
    return len(rows)


async def reconcile_in_transaction(
    session: AsyncSession, *, now: datetime | None = None
) -> ReconcileReport:
    """Run every reconciliation step inside the caller's transaction."""
    return ReconcileReport(
        idempotency_keys_expired=await purge_expired_idempotency_keys(session, now=now),
        recommendations_expired=await expire_recommendations(session, now=now),
    )


async def reconcile_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> ReconcileReport:
    """One reconciliation round in its own transaction.

    ``now`` overrides the database clock (tests); by default ``now()`` is used.
    ``settings`` is part of the stable signature for later reconciliation
    steps (Task 13) that depend on configuration.
    """
    async with session_factory() as session, session.begin():
        return await reconcile_in_transaction(session, now=now)
