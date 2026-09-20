"""Shared pytest fixtures.

Non-integration tests never touch the database: only fixtures requested by
a test that is (or depends on something that is) marked ``integration``
should create engines or run migrations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.base import Base
from app.db.session import get_engine, get_session_factory
from app.main import create_app
from app.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent

_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://linesense_app:dev-app-only@127.0.0.1:55432/linesense_test"
)
_DEFAULT_TEST_MIGRATION_DATABASE_URL = (
    "postgresql+psycopg://linesense_owner:dev-owner-only@127.0.0.1:55432/linesense_test"
)


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Settings configured for the test environment against linesense_test."""
    test_database_url = os.environ.get("LS_TEST_DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)
    test_migration_database_url = os.environ.get(
        "LS_TEST_MIGRATION_DATABASE_URL", _DEFAULT_TEST_MIGRATION_DATABASE_URL
    )
    return Settings(
        _env_file=None,
        environment="test",
        database_url=test_database_url,
        migration_database_url=test_migration_database_url,
        test_database_url=test_database_url,
        test_migration_database_url=test_migration_database_url,
        # The real fastembed model is a one-off, deliberate network download
        # (see docs/architecture/retrieval.md); tests always use the
        # deterministic HashingEmbedder instead (task-17-brief.md).
        embedder="hashing",
        # An HTTP-driven document upload test writes real files; keep them
        # under pytest's own tmp dir, never the repo's `.local/documents`.
        document_storage_dir=str(tmp_path_factory.mktemp("document-storage")),
    )


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-x", f"db_url={db_url}", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def migrated_db(settings: Settings) -> None:
    """Reset the test database to a known migration state (head).

    Only requested by fixtures/tests that need the database, so plain unit
    tests never pay for or require a running PostgreSQL cluster.
    """
    db_url = settings.test_migration_database_url
    unreachable_hint = (
        "Could not run migrations against the test database at "
        f"{db_url}. Is the local cluster running? Try `make db-init` "
        "(or `make db-start` if it's already initialized)."
    )
    try:
        down = _run_alembic(db_url, "downgrade", "base")
    except FileNotFoundError as exc:
        pytest.fail(f"{unreachable_hint}\nalembic was not found: {exc}")
    if down.returncode != 0:
        pytest.fail(
            f"{unreachable_hint}\n\nalembic downgrade base failed:\n{down.stdout}{down.stderr}"
        )

    up = _run_alembic(db_url, "upgrade", "head")
    if up.returncode != 0:
        pytest.fail(f"{unreachable_hint}\n\nalembic upgrade head failed:\n{up.stdout}{up.stderr}")


@pytest_asyncio.fixture(scope="session")
async def db_engine(settings: Settings, migrated_db: None) -> AsyncGenerator[AsyncEngine, None]:
    """Engine using the app role, against the migrated test database."""
    engine = get_engine(settings.database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def owner_engine(settings: Settings, migrated_db: None) -> AsyncGenerator[AsyncEngine, None]:
    """Engine using the owner role, against the migrated test database."""
    engine = get_engine(settings.migration_database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    # `str(url)` masks the password (renders it as `***`); this fixture needs
    # the real credentials to open a new connection against the same target
    # `db_engine` is bound to.
    session_factory = get_session_factory(db_engine.url.render_as_string(hide_password=False))
    async with session_factory() as session:
        yield session


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """App-role session factory for helpers that manage their own transactions."""
    return get_session_factory(db_engine.url.render_as_string(hide_password=False))


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tables_before_integration_tests(
    request: pytest.FixtureRequest, settings: Settings
) -> AsyncGenerator[None, None]:
    """Before each `integration`-marked test, truncate every mapped table.

    Plain unit tests never trigger migrations or a database connection:
    `migrated_db` (a sync fixture) is fetched lazily, and the engine is
    built directly via the cached `get_engine`, so this never depends on
    the async `owner_engine`/`db_engine` fixtures from inside another async
    fixture (pytest-asyncio cannot nest its event-loop runner that way).
    Before Task 3 creates any ORM models, `Base.metadata.sorted_tables` is
    empty, so the truncate itself is a no-op.
    """
    if "integration" not in request.keywords:
        yield
        return

    request.getfixturevalue("migrated_db")
    owner_engine = get_engine(settings.migration_database_url)
    tables = [table for table in Base.metadata.sorted_tables if table.name != "alembic_version"]
    if tables:
        table_names = ", ".join(f'"{table.name}"' for table in tables)
        async with owner_engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def app(settings: Settings):  # noqa: ANN201
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:  # noqa: ANN001
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
