"""Scoped hybrid search and citation routes (backend-contracts.md section 5;
task-17-brief.md req. 3-4). Both need `document:read`; the search/citation
scope is always the caller's own roles for the named factory.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.errors import AppError
from app.api.schemas.documents import CitationOut, DocumentRef, SearchResponse, SearchResultOut
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Factory
from app.db.session import get_db_session
from app.retrieval.embedder import build_embedder
from app.retrieval.search import RetrievalScope, get_citation, search

router = APIRouter(prefix="/api/v1", tags=["search"])

SEARCH_PERMISSION = "document:read"


@router.get("/factories/{factory_id}/search", response_model=SearchResponse)
async def search_documents(
    factory_id: uuid.UUID,
    request: Request,
    q: str = Query(..., min_length=1),
    k: int = Query(6, ge=1, le=20),
    mode: Literal["hybrid", "lexical", "vector"] = Query("hybrid"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SearchResponse:
    await load_scoped(session, Factory, factory_id, principal, SEARCH_PERMISSION)
    if not q.strip():
        raise AppError(422, "VALIDATION_ERROR", "q must not be blank.")

    scope = RetrievalScope(
        organization_id=principal.organization_id,
        factory_id=factory_id,
        roles=principal.roles_for(factory_id),
    )
    settings = request.app.state.settings
    results = await search(session, scope, q, k=k, mode=mode, embedder=build_embedder(settings))
    return SearchResponse(
        query=q,
        mode=mode,
        items=[
            SearchResultOut(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                document_slug=item.document_slug,
                document_version_id=item.document_version_id,
                version_no=item.version_no,
                title=item.title,
                page_number=item.page_number,
                section=item.section,
                text=item.text,
                score=item.score,
                lexical_rank=item.lexical_rank,
                vector_rank=item.vector_rank,
            )
            for item in results
        ],
    )


@router.get("/citations/{chunk_id}", response_model=CitationOut)
async def get_chunk_citation(
    chunk_id: uuid.UUID,
    factory_id: uuid.UUID = Query(...),
    run_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> CitationOut:
    await load_scoped(session, Factory, factory_id, principal, SEARCH_PERMISSION)
    scope = RetrievalScope(
        organization_id=principal.organization_id,
        factory_id=factory_id,
        roles=principal.roles_for(factory_id),
    )
    citation = await get_citation(session, scope, chunk_id, run_id=run_id)
    if citation is None:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    return CitationOut(
        chunk_id=citation.chunk_id,
        document=DocumentRef(
            id=citation.document_id, slug=citation.document_slug, title=citation.title
        ),
        version_no=citation.version_no,
        status=citation.status,
        page_number=citation.page_number,
        section=citation.section,
        text=citation.text,
    )
