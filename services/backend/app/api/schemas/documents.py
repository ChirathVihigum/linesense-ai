"""Request/response schemas for `app.api.documents` and `app.api.search`."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentVersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    status: str
    media_type: str
    size_bytes: int
    page_count: int | None
    rejection_reason: str | None
    created_at: datetime
    activated_at: datetime | None


class DocumentSummary(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    doc_type: str
    factory_id: uuid.UUID | None
    acl_roles: list[str]
    latest_version: DocumentVersionOut | None


class DocumentDetail(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    doc_type: str
    factory_id: uuid.UUID | None
    acl_roles: list[str]
    versions: list[DocumentVersionOut]


class DocumentUploadResult(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    slug: str
    version_no: int
    status: str


class SearchResultOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_slug: str
    document_version_id: uuid.UUID
    version_no: int
    title: str
    page_number: int | None
    section: str | None
    text: str
    score: float
    lexical_rank: int | None
    vector_rank: int | None


class SearchResponse(BaseModel):
    query: str
    mode: str
    items: list[SearchResultOut]


class DocumentRef(BaseModel):
    id: uuid.UUID
    slug: str
    title: str


class CitationOut(BaseModel):
    chunk_id: uuid.UUID
    document: DocumentRef
    version_no: int
    status: str
    page_number: int | None
    section: str | None
    text: str
