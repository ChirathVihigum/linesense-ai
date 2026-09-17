"""Document and retrieval tables: documents, versions, ACLs, chunks.

See ``docs/architecture/backend-contracts.md`` section 2 ("Documents and retrieval").
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    composite_factory_fk,
    created_at,
    enum_check,
    factory_id_col,
    org_factory_index,
    org_fk,
    uuid_pk,
)
from app.domain.vocab import DocumentType, DocumentVersionStatus


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        composite_factory_fk("documents"),
        org_factory_index("documents"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_documents_organization_id_slug"),
        enum_check("doc_type_valid", "doc_type", [t.value for t in DocumentType]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID | None] = factory_id_col(nullable=True)
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        sa.UniqueConstraint(
            "document_id", "version_no", name="uq_document_versions_document_id_version_no"
        ),
        sa.Index("ix_document_versions_document_id_status", "document_id", "status"),
        enum_check("status_valid", "status", [s.value for s in DocumentVersionStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("documents.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    media_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()
    activated_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )


class DocumentAcl(Base):
    __tablename__ = "document_acl"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "role", name="uq_document_acl_document_id_role"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        composite_factory_fk("chunks"),
        org_factory_index("chunks"),
        sa.UniqueConstraint(
            "document_version_id",
            "chunk_index",
            name="uq_chunks_document_version_id_chunk_index",
        ),
        sa.Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID | None] = factory_id_col(nullable=True)
    chunk_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        sa.Computed("to_tsvector('english', coalesce(section, '') || ' ' || text)", persisted=True),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
