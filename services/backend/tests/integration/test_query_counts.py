"""Query-count regression tests for list endpoints (task-25-brief.md req. 7).

For each endpoint, the same request shape is issued against a small dataset
and a larger one; the number of SQL statements it takes must be identical
either way (bounded by a small constant of round trips: scope check, main
query, and any batched lookups -- never one query *per row*). A regression
back to a per-row query (N+1) would grow this count with the data instead of
holding still, which is exactly what these tests would catch.

Statements are counted with a real ``before_cursor_execute`` listener on the
app's actual (cached-by-URL, so shared with the HTTP request) engine, not by
reading source code -- so this is a black-box check of what the endpoint
really executes, not an assumption about it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.retrieval.pipeline import create_document_upload
from app.retrieval.storage import DocumentStorage
from tests.factories import (
    make_balance,
    make_line,
    make_material,
    make_order,
    make_recommendation,
    make_run,
    make_slot,
)
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


@contextmanager
def count_statements(engine: AsyncEngine) -> Iterator[dict[str, int]]:
    counter = {"n": 0}

    def _before_cursor_execute(*_args: object, **_kwargs: object) -> None:
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)


async def _get_count(
    client: AsyncClient, db_engine: AsyncEngine, url: str, **kwargs: object
) -> tuple[int, int]:
    """``(statement_count, http_status)`` for one GET."""
    with count_statements(db_engine) as counter:
        response = await client.get(url, **kwargs)  # type: ignore[arg-type]
    return counter["n"], response.status_code


async def test_orders_list_query_count_is_independent_of_row_count(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    identity: IdentityFixture,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ktn = identity.factories["KTN"]
    for _ in range(3):
        await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()
    planner = await login_as(client, session_factory, "planner@demo.test")
    url = f"/api/v1/factories/{ktn.id}/orders"

    small_count, small_status = await _get_count(planner.client, db_engine, url)
    assert small_status == 200

    for _ in range(9):
        await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()

    large_count, large_status = await _get_count(planner.client, db_engine, url)
    assert large_status == 200
    assert large_count == small_count, (
        f"orders list issued {small_count} statements for 3 rows but {large_count} for 12"
    )


async def test_materials_overview_query_count_is_independent_of_row_count(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    identity: IdentityFixture,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ktn = identity.factories["KTN"]
    for _ in range(3):
        material = await make_material(db_session, organization=identity.organization)
        await make_balance(
            db_session, organization=identity.organization, factory=ktn, material=material
        )
    await db_session.commit()
    planner = await login_as(client, session_factory, "planner@demo.test")
    url = f"/api/v1/factories/{ktn.id}/materials"

    small_count, small_status = await _get_count(planner.client, db_engine, url)
    assert small_status == 200

    for _ in range(9):
        material = await make_material(db_session, organization=identity.organization)
        await make_balance(
            db_session, organization=identity.organization, factory=ktn, material=material
        )
    await db_session.commit()

    large_count, large_status = await _get_count(planner.client, db_engine, url)
    assert large_status == 200
    assert large_count == small_count, (
        f"materials overview issued {small_count} statements for 3 rows but {large_count} for 12"
    )


async def test_capacity_board_query_count_is_independent_of_slot_count(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    identity: IdentityFixture,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ktn = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=ktn)
    for day in range(3):
        await make_slot(db_session, line=line, slot_date=date(2026, 12, 1 + day), shift_code="A")
    await db_session.commit()
    planner = await login_as(client, session_factory, "planner@demo.test")
    url = f"/api/v1/factories/{ktn.id}/capacity"
    params = {"start": "2026-12-01", "end": "2026-12-31"}

    small_count, small_status = await _get_count(planner.client, db_engine, url, params=params)
    assert small_status == 200

    for day in range(3, 12):
        await make_slot(db_session, line=line, slot_date=date(2026, 12, 1 + day), shift_code="A")
    await db_session.commit()

    large_count, large_status = await _get_count(planner.client, db_engine, url, params=params)
    assert large_status == 200
    assert large_count == small_count, (
        f"capacity board issued {small_count} statements for 3 slots but {large_count} for 12"
    )


async def test_recommendations_list_query_count_is_independent_of_row_count(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    identity: IdentityFixture,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ktn = identity.factories["KTN"]
    for _ in range(3):
        order = await make_order(db_session, organization=identity.organization, factory=ktn)
        run = await make_run(db_session, order=order)
        await make_recommendation(db_session, run=run)
    await db_session.commit()
    planner = await login_as(client, session_factory, "planner@demo.test")
    url = f"/api/v1/factories/{ktn.id}/recommendations"

    small_count, small_status = await _get_count(planner.client, db_engine, url)
    assert small_status == 200

    for _ in range(9):
        order = await make_order(db_session, organization=identity.organization, factory=ktn)
        run = await make_run(db_session, order=order)
        await make_recommendation(db_session, run=run)
    await db_session.commit()

    large_count, large_status = await _get_count(planner.client, db_engine, url)
    assert large_status == 200
    assert large_count == small_count, (
        f"recommendations list issued {small_count} statements for 3 rows but {large_count} for 12"
    )


async def test_documents_list_query_count_is_independent_of_row_count(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    identity: IdentityFixture,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    from tests.agents.test_document_tool import _principal  # reuse the Principal builder

    ktn = identity.factories["KTN"]
    storage = DocumentStorage(tmp_path)
    principal = _principal(identity, factory_id=ktn.id, role="planner")

    async def _upload(n: int) -> None:
        await create_document_upload(
            db_session,
            principal,
            ktn.id,
            title=f"Doc {n} {uuid.uuid4().hex[:8]}",
            doc_type="OTHER",
            slug=f"doc-{n}-{uuid.uuid4().hex[:8]}",
            acl_roles=[],
            filename=f"doc-{n}.md",
            data=f"# Doc {n}\ncontent".encode(),
            storage=storage,
        )

    for n in range(3):
        await _upload(n)
    await db_session.commit()
    planner = await login_as(client, session_factory, "planner@demo.test")
    url = f"/api/v1/factories/{ktn.id}/documents"

    small_count, small_status = await _get_count(planner.client, db_engine, url)
    assert small_status == 200

    for n in range(3, 12):
        await _upload(n)
    await db_session.commit()

    large_count, large_status = await _get_count(planner.client, db_engine, url)
    assert large_status == 200
    assert large_count == small_count, (
        f"documents list issued {small_count} statements for 3 rows but {large_count} for 12"
    )
