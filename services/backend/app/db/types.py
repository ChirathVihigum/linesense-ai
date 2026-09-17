"""Shared column helpers used by every model in ``app.db.models``.

Keeping these in one place means every table's primary key, timestamp, and
tenant-scoping columns are declared identically, and the hand-written initial
migration (``migrations/versions/0001_initial_schema.py``) can mirror them
column-for-column without drift.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    """A ``uuid`` primary key generated in Python via ``uuid.uuid4``."""
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def uuid_col(*, nullable: bool = False, unique: bool = False) -> Mapped[uuid.UUID]:
    """A plain ``uuid`` column with no foreign key."""
    return mapped_column(PGUUID(as_uuid=True), nullable=nullable, unique=unique)


def created_at() -> Mapped[datetime]:
    """``created_at timestamptz not null default now()`` (the ``ts`` shorthand)."""
    return mapped_column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now())


def updated_at() -> Mapped[datetime]:
    """``updated_at timestamptz not null default now()``, refreshed on update."""
    return mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


def timestamptz(*, nullable: bool = False) -> Mapped[datetime]:
    """A plain ``timestamptz`` column with no default."""
    return mapped_column(sa.TIMESTAMP(timezone=True), nullable=nullable)


def org_fk(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    """``organization_id uuid not null fk organizations`` (the ``org`` shorthand)."""
    return mapped_column(PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=nullable)


def factory_id_col(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    """A bare ``factory_id`` column.

    No single-column foreign key is attached here: tables that also carry
    ``organization_id`` get a composite ``ForeignKeyConstraint`` in
    ``__table_args__`` instead, so a row cannot reference a factory belonging
    to a different organization. See ``composite_factory_fk``.
    """
    return mapped_column(PGUUID(as_uuid=True), nullable=nullable)


def composite_factory_fk(table_name: str) -> sa.ForeignKeyConstraint:
    """``(organization_id, factory_id) -> factories(organization_id, id)``.

    Postgres ``MATCH SIMPLE`` (the default) means this is only enforced when
    ``factory_id`` is non-null, so it is safe to use even on tables where
    ``factory_id`` is nullable (``documents``, ``chunks``).
    """
    return sa.ForeignKeyConstraint(
        ["organization_id", "factory_id"],
        ["factories.organization_id", "factories.id"],
        name=f"fk_{table_name}_organization_id_factory_id_factories",
    )


def money(precision: int, scale: int) -> sa.Numeric[Decimal]:
    """A ``Numeric`` alias for quantity/currency columns (never ``float``)."""
    return sa.Numeric(precision, scale)


def enum_check(name: str, column: str, values: Iterable[str]) -> sa.CheckConstraint:
    """A ``CHECK (<column> IN (<values>))`` constraint.

    ``name`` is the short rule name; the table's naming convention
    (``ck_%(table_name)s_%(constraint_name)s``) expands it to
    ``ck_<table>_<name>`` once attached to a table via ``__table_args__``.
    """
    rendered = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({rendered})", name=name)
