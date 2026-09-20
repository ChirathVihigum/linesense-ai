"""Document upload/list/detail/download routes (backend-contracts.md sections 2/4-5;
task-17-brief.md req. 1, 4).

Reads need `document:read` (every role); uploads need `document:upload`
(org_admin, supervisor, quality_manager, ie_engineer) and, for an
organization-wide upload (`scope=org`), specifically org_admin or
supervisor. A document with no ACL rows is visible to everyone with
`document:read` in scope; ACL rows restrict it to those roles (checked
against the caller's roles at the document's own factory, or across every
factory the caller holds a role in for an organization-wide document) --
a caller outside the ACL sees 404, never 403 (existence is never revealed,
matching every other resource's scoping).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.documents import (
    DocumentDetail,
    DocumentSummary,
    DocumentUploadResult,
    DocumentVersionOut,
)
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Factory
from app.db.models.documents import Document, DocumentAcl, DocumentVersion
from app.db.session import get_db_session
from app.domain.vocab import ROLES, DocumentType, DocumentVersionStatus, Role
from app.retrieval.pipeline import create_document_upload
from app.retrieval.storage import DocumentStorage
from app.settings import resolve_backend_path

router = APIRouter(tags=["documents"])

UPLOAD_PERMISSION = "document:upload"
READ_PERMISSION = "document:read"
_ORG_SCOPE_ROLES = frozenset({Role.ORG_ADMIN.value, Role.SUPERVISOR.value})
_VALID_SCOPES = frozenset({"factory", "org"})


def _storage_for(request: Request) -> DocumentStorage:
    settings = request.app.state.settings
    return DocumentStorage(resolve_backend_path(settings.document_storage_dir))


def _parse_acl_roles(raw: str) -> list[str]:
    roles = [role.strip() for role in raw.split(",") if role.strip()]
    unknown = [role for role in roles if role not in ROLES]
    if unknown:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"Unknown role(s) in acl_roles: {', '.join(unknown)}.",
            field_errors=[
                {"field": "acl_roles", "message": f"Unknown role {role!r}."} for role in unknown
            ],
        )
    return roles


def _acl_relevant_roles(principal: Principal, document: Document) -> frozenset[str]:
    """Roles the ACL check is evaluated against for this document's scope."""
    if document.factory_id is not None:
        return principal.roles_for(document.factory_id)
    everywhere: set[str] = set()
    for roles in principal.roles_by_factory.values():
        everywhere |= roles
    return frozenset(everywhere)


async def _acl_roles_for(session: AsyncSession, document_id: uuid.UUID) -> list[str]:
    rows = (
        await session.scalars(
            select(DocumentAcl.role)
            .where(DocumentAcl.document_id == document_id)
            .order_by(DocumentAcl.role)
        )
    ).all()
    return list(rows)


async def _load_document_visible(
    session: AsyncSession,
    document_id: uuid.UUID,
    principal: Principal,
    permission: str = READ_PERMISSION,
) -> Document:
    document = await load_scoped(session, Document, document_id, principal, permission)
    acl_roles = await _acl_roles_for(session, document.id)
    if acl_roles and not (_acl_relevant_roles(principal, document) & set(acl_roles)):
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    return document


async def _load_version_visible(
    session: AsyncSession,
    version_id: uuid.UUID,
    principal: Principal,
    permission: str = READ_PERMISSION,
) -> tuple[DocumentVersion, Document]:
    version = await session.get(DocumentVersion, version_id)
    if version is None:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    document = await _load_document_visible(session, version.document_id, principal, permission)
    return version, document


def _version_out(version: DocumentVersion) -> DocumentVersionOut:
    return DocumentVersionOut(
        id=version.id,
        version_no=version.version_no,
        status=version.status,
        media_type=version.media_type,
        size_bytes=version.size_bytes,
        page_count=version.page_count,
        rejection_reason=version.rejection_reason,
        created_at=version.created_at,
        activated_at=version.activated_at,
    )


@router.post(
    "/api/v1/factories/{factory_id}/documents",
    response_model=DocumentUploadResult,
    status_code=202,
)
async def upload_document(
    factory_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_type: str = Form(...),
    slug: str = Form(...),
    scope: str = Form(...),
    acl_roles: str = Form(""),
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    try:
        await load_scoped(session, Factory, factory_id, principal, UPLOAD_PERMISSION)
        if scope not in _VALID_SCOPES:
            raise AppError(422, "VALIDATION_ERROR", "scope must be 'factory' or 'org'.")
        if doc_type not in {member.value for member in DocumentType}:
            raise AppError(422, "VALIDATION_ERROR", f"Unknown doc_type {doc_type!r}.")
        if scope == "org" and not (principal.roles_for(factory_id) & _ORG_SCOPE_ROLES):
            raise AppError(
                403, "FORBIDDEN", "Organization-wide uploads require org_admin or supervisor."
            )
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=factory_id,
            action="document.upload",
            target_type="document",
            target_id=slug,
        )
        raise

    parsed_acl_roles = _parse_acl_roles(acl_roles)
    document_factory_id = factory_id if scope == "factory" else None
    settings = request.app.state.settings

    # Read at most one byte past the limit so an oversized file is never
    # fully buffered into memory (matches app.api.imports's CSV upload).
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise AppError(413, "PAYLOAD_TOO_LARGE", "File exceeds the maximum upload size.")

    early = await idempotent_start(
        session,
        principal,
        operation="document:upload",
        key=idempotency_key,
        request_payload={
            "factory_id": str(factory_id),
            "slug": slug,
            "scope": scope,
            "sha256_input_len": len(data),
        },
    )
    if early is not None:
        return early

    version = await create_document_upload(
        session,
        principal,
        document_factory_id,
        title=title,
        doc_type=doc_type,
        slug=slug,
        acl_roles=parsed_acl_roles,
        filename=file.filename or slug,
        data=data,
        storage=_storage_for(request),
    )

    body = DocumentUploadResult(
        document_id=version.document_id,
        version_id=version.id,
        slug=slug,
        version_no=version.version_no,
        status=version.status,
    )
    return await idempotent_finish(
        session,
        principal,
        operation="document:upload",
        key=idempotency_key,
        status_code=202,
        body=body,
    )


@router.get("/api/v1/factories/{factory_id}/documents", response_model=Page[DocumentSummary])
async def list_documents(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[DocumentSummary]:
    await load_scoped(session, Factory, factory_id, principal, READ_PERMISSION)

    stmt = select(Document).where(
        Document.organization_id == principal.organization_id,
        (Document.factory_id.is_(None)) | (Document.factory_id == factory_id),
    )
    documents = (await session.scalars(stmt.order_by(Document.slug))).all()
    if not documents:
        return Page[DocumentSummary](items=[], total=0, limit=page.limit, offset=page.offset)

    acl_rows = (
        await session.execute(
            select(DocumentAcl.document_id, DocumentAcl.role).where(
                DocumentAcl.document_id.in_([document.id for document in documents])
            )
        )
    ).all()
    acl_by_document: dict[uuid.UUID, set[str]] = {}
    for document_id, role in acl_rows:
        acl_by_document.setdefault(document_id, set()).add(role)

    visible = [
        document
        for document in documents
        if not (
            document.id in acl_by_document
            and not (_acl_relevant_roles(principal, document) & acl_by_document[document.id])
        )
    ]

    latest_versions: dict[uuid.UUID, DocumentVersion] = {}
    if visible:
        version_rows = (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id.in_([document.id for document in visible]))
                .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
            )
        ).all()
        for version in version_rows:
            latest_versions.setdefault(version.document_id, version)

    total = len(visible)
    page_slice = visible[page.offset : page.offset + page.limit]
    items = [
        DocumentSummary(
            id=document.id,
            slug=document.slug,
            title=document.title,
            doc_type=document.doc_type,
            factory_id=document.factory_id,
            acl_roles=sorted(acl_by_document.get(document.id, set())),
            latest_version=(
                _version_out(latest_versions[document.id])
                if document.id in latest_versions
                else None
            ),
        )
        for document in page_slice
    ]
    return Page[DocumentSummary](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/api/v1/documents/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> DocumentDetail:
    document = await _load_document_visible(session, document_id, principal)
    versions = (
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_no.desc())
        )
    ).all()
    acl_roles = await _acl_roles_for(session, document.id)
    return DocumentDetail(
        id=document.id,
        slug=document.slug,
        title=document.title,
        doc_type=document.doc_type,
        factory_id=document.factory_id,
        acl_roles=acl_roles,
        versions=[_version_out(version) for version in versions],
    )


@router.get("/api/v1/document-versions/{version_id}", response_model=DocumentVersionOut)
async def get_document_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> DocumentVersionOut:
    version, _document = await _load_version_visible(session, version_id, principal)
    return _version_out(version)


@router.get("/api/v1/document-versions/{version_id}/download")
async def download_document_version(
    version_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    version, _document = await _load_version_visible(session, version_id, principal)
    if version.status not in (
        DocumentVersionStatus.ACTIVE.value,
        DocumentVersionStatus.SUPERSEDED.value,
    ):
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    storage = _storage_for(request)
    data = storage.read_store(version.storage_key)
    return Response(
        content=data,
        media_type=version.media_type,
        headers={
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
        },
    )
