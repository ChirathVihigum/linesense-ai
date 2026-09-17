"""Operational support tables: imports, idempotency keys, notifications, notes.

See ``docs/architecture/backend-contracts.md`` section 2 ("Decisions and operations").
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    composite_factory_fk,
    created_at,
    enum_check,
    factory_id_col,
    org_fk,
    uuid_pk,
)
from app.domain.vocab import ImportBatchStatus


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        composite_factory_fk("import_batches"),
        sa.Index(
            "uq_import_batches_organization_id_kind_file_sha256",
            "organization_id",
            "kind",
            "file_sha256",
            unique=True,
            postgresql_where=sa.text("status = 'COMMITTED'"),
        ),
        enum_check("status_valid", "status", [s.value for s in ImportBatchStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    preview: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at()
    committed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )


class ImportRowError(Base):
    __tablename__ = "import_errors"

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    field: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    message: Mapped[str] = mapped_column(sa.Text, nullable=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "actor_id",
            "operation",
            "key",
            name="uq_idempotency_keys_organization_id_actor_id_operation_key",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    actor_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    operation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (composite_factory_fk("notifications"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    role: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    link: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = created_at()
    read_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (composite_factory_fk("notes"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    author_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    classification: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()
