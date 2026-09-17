from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.db.models import IdempotencyKey
from app.idempotency.service import StoredResponse, begin, finish, request_hash
from tests.factories import make_org

pytestmark = pytest.mark.integration

OPERATION = "order:create"
KEY = "key-0001-abcdef"


async def test_begin_finish_replay_and_reuse(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    payload = {"quantity": 10, "style": "A"}

    first = await begin(
        db_session,
        organization_id=org.id,
        actor_id="user-1",
        operation=OPERATION,
        key=KEY,
        request_payload=payload,
    )
    assert first is None

    row = await db_session.scalar(select(IdempotencyKey))
    assert row is not None
    assert row.request_hash == request_hash(payload)
    assert row.response_status is None
    expected_expiry = datetime.now(tz=UTC) + timedelta(hours=24)
    assert abs((row.expires_at - expected_expiry).total_seconds()) < 60

    await finish(
        db_session,
        organization_id=org.id,
        actor_id="user-1",
        operation=OPERATION,
        key=KEY,
        status_code=201,
        body={"id": "order-1"},
    )
    await db_session.commit()

    # Key order of the payload must not matter (canonical JSON).
    replay = await begin(
        db_session,
        organization_id=org.id,
        actor_id="user-1",
        operation=OPERATION,
        key=KEY,
        request_payload={"style": "A", "quantity": 10},
    )
    assert replay == StoredResponse(status_code=201, body={"id": "order-1"})

    with pytest.raises(AppError) as excinfo:
        await begin(
            db_session,
            organization_id=org.id,
            actor_id="user-1",
            operation=OPERATION,
            key=KEY,
            request_payload={"quantity": 11, "style": "A"},
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "IDEMPOTENCY_KEY_REUSED"


async def test_keys_are_scoped_per_actor(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    for actor in ("user-1", "user-2"):
        result = await begin(
            db_session,
            organization_id=org.id,
            actor_id=actor,
            operation=OPERATION,
            key=KEY,
            request_payload={"actor": actor},
        )
        assert result is None
    await finish(
        db_session,
        organization_id=org.id,
        actor_id="user-1",
        operation=OPERATION,
        key=KEY,
        status_code=201,
        body={"who": "user-1"},
    )
    await db_session.commit()

    rows = (await db_session.scalars(select(IdempotencyKey))).all()
    assert len(rows) == 2
    # user-2's key is still pending and independent of user-1's stored response.
    with pytest.raises(AppError) as excinfo:
        await begin(
            db_session,
            organization_id=org.id,
            actor_id="user-2",
            operation=OPERATION,
            key=KEY,
            request_payload={"actor": "user-2"},
        )
    assert excinfo.value.code == "CONFLICT"


async def test_pending_key_reports_in_progress(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    kwargs = {
        "organization_id": org.id,
        "actor_id": "user-1",
        "operation": OPERATION,
        "key": KEY,
        "request_payload": {"a": 1},
    }
    assert await begin(db_session, **kwargs) is None  # type: ignore[arg-type]
    await db_session.commit()

    with pytest.raises(AppError) as excinfo:
        await begin(db_session, **kwargs)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "CONFLICT"
    assert "in progress" in excinfo.value.message


async def test_expired_key_starts_fresh(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    common = {
        "organization_id": org.id,
        "actor_id": "user-1",
        "operation": OPERATION,
        "key": KEY,
    }
    assert await begin(db_session, request_payload={"a": 1}, **common) is None  # type: ignore[arg-type]
    await finish(db_session, status_code=201, body={"old": True}, **common)  # type: ignore[arg-type]
    await db_session.execute(
        update(IdempotencyKey).values(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))
    )
    await db_session.commit()

    assert await begin(db_session, request_payload={"a": 2}, **common) is None  # type: ignore[arg-type]
    row = await db_session.scalar(select(IdempotencyKey))
    assert row is not None
    assert row.response_status is None
    assert row.request_hash == request_hash({"a": 2})


async def test_concurrent_duplicates_serialize(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as setup:
        org = await make_org(setup)
        await setup.commit()
    common = {
        "organization_id": org.id,
        "actor_id": "user-1",
        "operation": OPERATION,
        "key": KEY,
        "request_payload": {"a": 1},
    }
    first_started = asyncio.Event()

    async def first() -> None:
        async with session_factory() as session:
            assert await begin(session, **common) is None  # type: ignore[arg-type]
            first_started.set()
            await asyncio.sleep(0.3)
            await finish(
                session,
                organization_id=org.id,
                actor_id="user-1",
                operation=OPERATION,
                key=KEY,
                status_code=201,
                body={"done": True},
            )
            await session.commit()

    async def second() -> StoredResponse | None:
        await first_started.wait()
        async with session_factory() as session:
            # Blocks on the first transaction's uncommitted row, then sees its response.
            result = await begin(session, **common)  # type: ignore[arg-type]
            await session.commit()
            return result

    _, replay = await asyncio.gather(first(), second())
    assert replay == StoredResponse(status_code=201, body={"done": True})
