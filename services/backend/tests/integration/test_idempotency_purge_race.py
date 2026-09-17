"""``idempotency.begin`` survives its conflicting row being purged mid-claim."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey
from app.idempotency import service
from app.idempotency.service import begin, request_hash
from tests.factories import make_org

pytestmark = pytest.mark.integration


async def test_begin_retries_insert_when_conflicting_row_vanishes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await make_org(db_session)
    first = await begin(
        db_session,
        organization_id=org.id,
        actor_id="user-1",
        operation="order:create",
        key="k-1",
        request_payload={"a": 1},
    )
    assert first is None
    original_id = await db_session.scalar(select(IdempotencyKey.id))
    await db_session.commit()

    original_lock = service._lock_existing
    lock_calls = 0

    async def purge_then_lock(session: AsyncSession, **kwargs: object) -> IdempotencyKey | None:
        nonlocal lock_calls
        lock_calls += 1
        if lock_calls == 1:
            # Simulates the reconciler deleting the row between the two statements.
            await session.execute(delete(IdempotencyKey))
        return await original_lock(session, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_lock_existing", purge_then_lock)

    second = await begin(
        db_session,
        organization_id=org.id,
        actor_id="user-1",
        operation="order:create",
        key="k-1",
        request_payload={"a": 2},
    )
    assert second is None  # claimed afresh instead of raising NoResultFound
    assert lock_calls == 1
    rows = (await db_session.scalars(select(IdempotencyKey))).all()
    assert len(rows) == 1
    assert rows[0].id != original_id
    assert rows[0].request_hash == request_hash({"a": 2})
    assert isinstance(rows[0].id, uuid.UUID)


async def test_begin_gives_up_when_row_keeps_vanishing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await make_org(db_session)

    async def always_conflict(session: AsyncSession, **kwargs: object) -> bool:
        return False

    async def always_missing(session: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(service, "_insert_pending", always_conflict)
    monkeypatch.setattr(service, "_lock_existing", always_missing)

    with pytest.raises(RuntimeError, match="kept disappearing"):
        await begin(
            db_session,
            organization_id=org.id,
            actor_id="user-1",
            operation="order:create",
            key="k-2",
            request_payload={},
        )
