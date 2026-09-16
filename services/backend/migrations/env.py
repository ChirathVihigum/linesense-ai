"""Alembic environment: async engine, target metadata, and URL resolution.

The database URL comes from (in order of precedence):
1. ``-x db_url=...`` on the command line (used by tests, against the
   `linesense_owner` / test database).
2. ``Settings.migration_database_url`` (``LS_MIGRATION_DATABASE_URL``).

``app.db.models`` is imported (if it exists) so every ORM model registers
itself on ``Base.metadata`` before autogenerate/``alembic check`` run. The
import is guarded because that package does not exist until Task 3.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import Base
from app.settings import get_settings

if importlib.util.find_spec("app.db.models") is not None:
    importlib.import_module("app.db.models")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_db_url() -> str:
    cli_url = context.get_x_argument(as_dictionary=True).get("db_url")
    if cli_url:
        return cli_url
    return get_settings().migration_database_url


def run_migrations_offline() -> None:
    url = _resolve_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_db_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
