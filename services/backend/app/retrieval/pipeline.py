"""Upload intake and background processing of document versions (task-17-brief.md req. 1-2).

``create_document_upload`` (API-driven) and ``app.retrieval.loader``'s
corpus ingestion share :func:`_ingest`: hashing, duplicate detection,
document/version bookkeeping and quarantine storage. ``process_document_
version`` does the actual structural scan / extraction / chunking /
embedding and the QUARANTINE -> {REJECTED, ACTIVE} transition; it is safe to
call more than once for the same version (a lease-lost job retry): the
first phase's row lock plus a terminal-status guard makes every call after
the first a no-op, and the chunk-insert phase always replaces (rather than
appends to) that version's chunks.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.db.models import Factory, Membership, Notification, RoleAssignment
from app.db.models.documents import Chunk, Document, DocumentAcl, DocumentVersion
from app.domain.clock import utcnow
from app.domain.vocab import ActorType, AuditOutcome, DocumentVersionStatus
from app.jobs.queue import enqueue
from app.retrieval.chunking import chunk_sections
from app.retrieval.embedder import Embedder
from app.retrieval.extract import UnsupportedDocument, extract_text
from app.retrieval.storage import DocumentStorage

DOCUMENT_PROCESS_JOB = "document.process"
DOCUMENT_QUEUE = "document"
PROCESSING_FAILED_MESSAGE = "Processing failed"
EXTRACTION_TIMEOUT_SECONDS = 30.0
EMBED_BATCH_SIZE = 32
DUPLICATE_MESSAGE = "An active version with identical content already exists."

_TERMINAL_STATUSES = (
    DocumentVersionStatus.ACTIVE.value,
    DocumentVersionStatus.REJECTED.value,
    DocumentVersionStatus.SUPERSEDED.value,
)

_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9 ._-]")


def sniff_media_type(filename: str, data: bytes) -> str:
    """The extension and the sniffed content must agree (415 otherwise)."""
    suffix = Path(filename).suffix.lower()
    media_type = _MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise AppError(
            415, "UNSUPPORTED_MEDIA_TYPE", f"Unsupported file extension {suffix or '(none)'!r}."
        )
    if media_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "File is not a valid PDF.")
        return media_type
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "File is not valid UTF-8 text.") from exc
    if "\x00" in text:
        raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "File contains NUL bytes.")
    return media_type


def sanitize_filename(filename: str) -> str:
    """Filename kept only as audit metadata; never used to build a storage path."""
    base = Path(filename).name  # drops any directory components
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip(" .") or "upload"
    return cleaned[:255]


async def _document_for_slug(
    session: AsyncSession, *, organization_id: uuid.UUID, slug: str
) -> Document | None:
    document: Document | None = await session.scalar(
        select(Document).where(Document.organization_id == organization_id, Document.slug == slug)
    )
    return document


async def _next_version_no(session: AsyncSession, document_id: uuid.UUID) -> int:
    current_max = await session.scalar(
        select(sa.func.max(DocumentVersion.version_no)).where(
            DocumentVersion.document_id == document_id
        )
    )
    return int(current_max or 0) + 1


async def _has_active_duplicate(
    session: AsyncSession, *, document_id: uuid.UUID, sha256: str
) -> bool:
    existing = await session.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.sha256 == sha256,
            DocumentVersion.status == DocumentVersionStatus.ACTIVE.value,
        )
    )
    return existing is not None


async def _replace_acl(
    session: AsyncSession, *, document_id: uuid.UUID, acl_roles: list[str]
) -> None:
    await session.execute(sa.delete(DocumentAcl).where(DocumentAcl.document_id == document_id))
    for role in dict.fromkeys(acl_roles):
        session.add(DocumentAcl(document_id=document_id, role=role))


async def _ingest(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID | None,
    slug: str,
    title: str,
    doc_type: str,
    acl_roles: list[str],
    filename: str,
    data: bytes,
    created_by: uuid.UUID | None,
    storage: DocumentStorage,
) -> DocumentVersion | None:
    """Create the next pending version for ``slug``, or ``None`` on a duplicate hash."""
    media_type = sniff_media_type(filename, data)
    sha256 = hashlib.sha256(data).hexdigest()

    document = await _document_for_slug(session, organization_id=organization_id, slug=slug)
    if document is None:
        document = Document(
            organization_id=organization_id,
            factory_id=factory_id,
            slug=slug,
            title=title,
            doc_type=doc_type,
            created_by=created_by,
        )
        session.add(document)
        await session.flush()
    elif document.factory_id != factory_id:
        raise AppError(409, "CONFLICT", f"Document {slug!r} already exists with a different scope.")

    if await _has_active_duplicate(session, document_id=document.id, sha256=sha256):
        return None

    version_no = await _next_version_no(session, document.id)
    storage_key = storage.put(key=uuid.uuid4().hex, data=data)
    version = DocumentVersion(
        document_id=document.id,
        version_no=version_no,
        status=DocumentVersionStatus.QUARANTINE.value,
        sha256=sha256,
        storage_key=storage_key,
        media_type=media_type,
        size_bytes=len(data),
        created_by=created_by,
    )
    session.add(version)
    await _replace_acl(session, document_id=document.id, acl_roles=acl_roles)
    await session.flush()
    return version


async def create_document_upload(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID | None,
    *,
    title: str,
    doc_type: str,
    slug: str,
    acl_roles: list[str],
    filename: str,
    data: bytes,
    storage: DocumentStorage,
) -> DocumentVersion:
    """Quarantine an upload, enqueue ``document.process`` and audit it (one transaction)."""
    version = await _ingest(
        session,
        organization_id=principal.organization_id,
        factory_id=factory_id,
        slug=slug,
        title=title,
        doc_type=doc_type,
        acl_roles=acl_roles,
        filename=filename,
        data=data,
        created_by=principal.user_id,
        storage=storage,
    )
    if version is None:
        raise AppError(409, "CONFLICT", DUPLICATE_MESSAGE)

    await enqueue(
        session,
        queue=DOCUMENT_QUEUE,
        job_type=DOCUMENT_PROCESS_JOB,
        payload={"document_version_id": str(version.id)},
        dedupe_key=f"document-process:{version.id}",
    )
    await record_audit(
        session,
        organization_id=principal.organization_id,
        factory_id=factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="document.upload",
        target_type="document_version",
        target_id=str(version.id),
        outcome=AuditOutcome.SUCCESS.value,
        after={
            "document_id": str(version.document_id),
            "slug": slug,
            "version_no": version.version_no,
            "filename": sanitize_filename(filename),
            "size_bytes": len(data),
        },
    )
    return version


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------


async def process_document_version(
    session_factory: async_sessionmaker[AsyncSession],
    version_id: uuid.UUID,
    *,
    embedder: Embedder,
    storage: DocumentStorage,
) -> None:
    async with session_factory() as session, session.begin():
        version = await session.get(DocumentVersion, version_id, with_for_update=True)
        if version is None or version.status in _TERMINAL_STATUSES:
            return
        document = await session.get(Document, version.document_id)
        if document is None:  # pragma: no cover - a version always has a document
            raise RuntimeError(f"document version {version_id} references a missing document")
        media_type = version.media_type
        storage_key = version.storage_key
        version.status = DocumentVersionStatus.PROCESSING.value

    data = storage.read_quarantine(storage_key)

    try:
        sections = await asyncio.wait_for(
            asyncio.to_thread(extract_text, data, media_type),
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except UnsupportedDocument as exc:
        await reject_document_version(
            session_factory, version_id, reason=exc.reason, storage=storage
        )
        return
    except TimeoutError:
        await reject_document_version(
            session_factory, version_id, reason="Extraction timed out.", storage=storage
        )
        return

    drafts = chunk_sections(sections)
    embeddings: list[list[float]] = []
    for start in range(0, len(drafts), EMBED_BATCH_SIZE):
        batch = [draft.text for draft in drafts[start : start + EMBED_BATCH_SIZE]]
        embeddings.extend(await asyncio.to_thread(embedder.embed, batch))

    page_count = max(
        (draft.page_number for draft in drafts if draft.page_number is not None), default=None
    )

    async with session_factory() as session, session.begin():
        version = await session.get(DocumentVersion, version_id, with_for_update=True)
        if version is None or version.status != DocumentVersionStatus.PROCESSING.value:
            return  # fenced out: a concurrent/retried attempt already resolved this version
        document = await session.get(Document, version.document_id)
        if document is None:  # pragma: no cover - a version always has a document
            raise RuntimeError(f"document version {version_id} references a missing document")

        # Idempotent against a lease-lost retry of this same phase: replace,
        # never append to, this version's chunks.
        await session.execute(sa.delete(Chunk).where(Chunk.document_version_id == version.id))
        for draft, vector in zip(drafts, embeddings, strict=True):
            session.add(
                Chunk(
                    document_version_id=version.id,
                    organization_id=document.organization_id,
                    factory_id=document.factory_id,
                    chunk_index=draft.chunk_index,
                    page_number=draft.page_number,
                    section=draft.section,
                    text=draft.text,
                    token_count=draft.token_count,
                    embedding=vector,
                    embedding_model=embedder.model_name,
                )
            )

        previous_active = await session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == version.document_id,
                DocumentVersion.status == DocumentVersionStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if previous_active is not None:
            previous_active.status = DocumentVersionStatus.SUPERSEDED.value

        version.status = DocumentVersionStatus.ACTIVE.value
        version.activated_at = utcnow()
        version.page_count = page_count
        storage.activate(storage_key)
        await session.flush()

        await record_audit(
            session,
            organization_id=document.organization_id,
            factory_id=document.factory_id,
            actor_type=ActorType.SYSTEM.value,
            actor_id="document-pipeline",
            action="document.activate",
            target_type="document_version",
            target_id=str(version.id),
            outcome=AuditOutcome.SUCCESS.value,
            after={"status": version.status, "chunk_count": len(drafts)},
        )


# --------------------------------------------------------------------------
# Rejection (structural-scan failure, extraction timeout, or job exhaustion)
# --------------------------------------------------------------------------


async def _notification_factory_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    document_factory_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """A concrete factory to notify the uploader on (notifications always need one).

    An org-wide document (``factory_id is None``) has no natural factory of
    its own, so this falls back to a factory the uploader actually holds a
    role in, then to any factory in the organization.
    """
    if document_factory_id is not None:
        return document_factory_id
    if user_id is not None:
        role_factory_id: uuid.UUID | None = await session.scalar(
            select(RoleAssignment.factory_id)
            .join(Membership, RoleAssignment.membership_id == Membership.id)
            .where(
                Membership.organization_id == organization_id,
                Membership.user_id == user_id,
                RoleAssignment.factory_id.is_not(None),
            )
            .limit(1)
        )
        if role_factory_id is not None:
            return role_factory_id
    any_factory_id: uuid.UUID | None = await session.scalar(
        select(Factory.id)
        .where(Factory.organization_id == organization_id)
        .order_by(Factory.code)
        .limit(1)
    )
    return any_factory_id


async def reject_document_version(
    session_factory: async_sessionmaker[AsyncSession],
    version_id: uuid.UUID,
    *,
    reason: str,
    storage: DocumentStorage,
) -> None:
    """Mark ``version_id`` REJECTED, remove it from quarantine, audit and notify.

    Idempotent: a version already in a terminal state (e.g. resolved by an
    earlier attempt before a lease was lost) is left untouched.
    """
    async with session_factory() as session, session.begin():
        version = await session.get(DocumentVersion, version_id, with_for_update=True)
        if version is None or version.status in _TERMINAL_STATUSES:
            return
        document = await session.get(Document, version.document_id)
        if document is None:  # pragma: no cover - a version always has a document
            raise RuntimeError(f"document version {version_id} references a missing document")

        version.status = DocumentVersionStatus.REJECTED.value
        version.rejection_reason = reason[:2000]
        storage.delete_quarantine(version.storage_key)

        await record_audit(
            session,
            organization_id=document.organization_id,
            factory_id=document.factory_id,
            actor_type=ActorType.SYSTEM.value,
            actor_id="document-pipeline",
            action="document.reject",
            target_type="document_version",
            target_id=str(version.id),
            outcome=AuditOutcome.SUCCESS.value,
            reason=reason,
        )

        notify_factory_id = await _notification_factory_id(
            session,
            organization_id=document.organization_id,
            document_factory_id=document.factory_id,
            user_id=version.created_by,
        )
        if version.created_by is not None and notify_factory_id is not None:
            session.add(
                Notification(
                    organization_id=document.organization_id,
                    factory_id=notify_factory_id,
                    user_id=version.created_by,
                    kind="document.rejected",
                    title=f"Document upload rejected: {document.title}",
                    body=reason,
                )
            )
