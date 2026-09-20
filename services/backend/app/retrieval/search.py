"""Scoped hybrid (lexical + vector) search and citation lookup (task-17-brief.md req. 3-4).

Both the lexical and vector candidate lists apply the *same* SQL filter
(:func:`_scope_filter`): organization, an org-wide-or-matching factory,
``ACTIVE`` version status, and the document ACL (no rows = readable by
everyone with ``document:read`` in scope; rows = only those roles). Hybrid
mode fuses the two ranked lists with reciprocal-rank fusion
(``sum(1 / (rrf_k + rank))`` over each list's top ``candidate_k``), ties
broken by chunk id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.db.models import AgentResultRecord, AgentTask, AnalysisRun
from app.db.models.documents import Chunk, Document, DocumentAcl, DocumentVersion
from app.domain.vocab import DocumentVersionStatus
from app.retrieval.embedder import Embedder

SearchMode = Literal["hybrid", "lexical", "vector"]

_COLUMNS = (
    Chunk.id,
    Chunk.document_version_id,
    Chunk.page_number,
    Chunk.section,
    Chunk.text,
    DocumentVersion.version_no,
    DocumentVersion.status,
    Document.id.label("document_id"),
    Document.slug.label("document_slug"),
    Document.title.label("title"),
)


@dataclass(frozen=True)
class RetrievalScope:
    organization_id: uuid.UUID
    factory_id: uuid.UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class RetrievedChunk:
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


@dataclass(frozen=True)
class CitationResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_slug: str
    title: str
    version_no: int
    status: str
    page_number: int | None
    section: str | None
    text: str


def _base_query(scope: RetrievalScope, *, statuses: tuple[str, ...]) -> sa.Select[Any]:
    acl_rows = select(DocumentAcl.id).where(DocumentAcl.document_id == Document.id)
    acl_match = select(DocumentAcl.id).where(
        DocumentAcl.document_id == Document.id, DocumentAcl.role.in_(scope.roles)
    )
    return (
        select(*_COLUMNS)
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(
            Chunk.organization_id == scope.organization_id,
            sa.or_(Chunk.factory_id.is_(None), Chunk.factory_id == scope.factory_id),
            DocumentVersion.status.in_(statuses),
            sa.or_(~acl_rows.exists(), acl_match.exists()),
        )
    )


async def _lexical_candidates(
    session: AsyncSession, scope: RetrievalScope, query: str, candidate_k: int
) -> list[Row[Any]]:
    tsquery = sa.func.websearch_to_tsquery("english", query)
    rank = sa.func.ts_rank_cd(Chunk.tsv, tsquery).label("rank_score")
    stmt = (
        _base_query(scope, statuses=(DocumentVersionStatus.ACTIVE.value,))
        .add_columns(rank)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), Chunk.id)
        .limit(candidate_k)
    )
    return list((await session.execute(stmt)).all())


async def _vector_candidates(
    session: AsyncSession,
    scope: RetrievalScope,
    query_vector: list[float],
    model_name: str,
    candidate_k: int,
) -> list[Row[Any]]:
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    stmt = (
        _base_query(scope, statuses=(DocumentVersionStatus.ACTIVE.value,))
        .add_columns(distance)
        .where(Chunk.embedding.is_not(None), Chunk.embedding_model == model_name)
        .order_by(distance.asc(), Chunk.id)
        .limit(candidate_k)
    )
    return list((await session.execute(stmt)).all())


def _to_chunk(
    row: Row[Any], *, score: float, lexical_rank: int | None, vector_rank: int | None
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row.id,
        document_id=row.document_id,
        document_slug=row.document_slug,
        document_version_id=row.document_version_id,
        version_no=row.version_no,
        title=row.title,
        page_number=row.page_number,
        section=row.section,
        text=row.text,
        score=score,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
    )


async def search(
    session: AsyncSession,
    scope: RetrievalScope,
    query: str,
    *,
    k: int = 6,
    mode: SearchMode = "hybrid",
    embedder: Embedder,
    candidate_k: int = 20,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    stripped = query.strip()
    if not stripped:
        raise AppError(422, "VALIDATION_ERROR", "Query must not be blank.")
    if mode not in ("hybrid", "lexical", "vector"):
        raise ValueError(f"unknown search mode {mode!r}")

    lexical_rows: list[Row[Any]] = []
    vector_rows: list[Row[Any]] = []
    if mode in ("hybrid", "lexical"):
        lexical_rows = await _lexical_candidates(session, scope, stripped, candidate_k)
    if mode in ("hybrid", "vector"):
        (query_vector,) = embedder.embed([stripped])
        vector_rows = await _vector_candidates(
            session, scope, query_vector, embedder.model_name, candidate_k
        )

    if mode == "lexical":
        return [
            _to_chunk(row, score=float(row.rank_score), lexical_rank=index + 1, vector_rank=None)
            for index, row in enumerate(lexical_rows[:k])
        ]
    if mode == "vector":
        return [
            _to_chunk(
                row, score=1.0 - float(row.distance), lexical_rank=None, vector_rank=index + 1
            )
            for index, row in enumerate(vector_rows[:k])
        ]

    entries: dict[uuid.UUID, dict[str, Any]] = {}
    for index, row in enumerate(lexical_rows):
        entry = entries.setdefault(row.id, {"row": row})
        entry["lexical_rank"] = index + 1
    for index, row in enumerate(vector_rows):
        entry = entries.setdefault(row.id, {"row": row})
        entry["vector_rank"] = index + 1

    def rrf_component(rank: int | None) -> float:
        return 1.0 / (rrf_k + rank) if rank is not None else 0.0

    fused: list[tuple[float, str, uuid.UUID, dict[str, Any]]] = []
    for chunk_id, entry in entries.items():
        score = rrf_component(entry.get("lexical_rank")) + rrf_component(entry.get("vector_rank"))
        fused.append((score, str(chunk_id), chunk_id, entry))
    fused.sort(key=lambda item: (-item[0], item[1]))

    return [
        _to_chunk(
            entry["row"],
            score=score,
            lexical_rank=entry.get("lexical_rank"),
            vector_rank=entry.get("vector_rank"),
        )
        for score, _, _, entry in fused[:k]
    ]


class ScopedRetrieval:
    """A :class:`app.agents.base.RetrievalPort` bound to one run's scope.

    Opens its own session per call (agent tool handlers only carry a
    ``session_factory``, not a live session — see ``app.orchestration.
    executor``).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        scope: RetrievalScope,
        embedder: Embedder,
    ) -> None:
        self._session_factory = session_factory
        self._scope = scope
        self._embedder = embedder

    async def search(self, query: str, k: int) -> list[RetrievedChunk]:
        async with self._session_factory() as session:
            return await search(session, self._scope, query, k=k, embedder=self._embedder)


async def get_citation(
    session: AsyncSession,
    scope: RetrievalScope,
    chunk_id: uuid.UUID,
    *,
    run_id: uuid.UUID | None = None,
) -> CitationResult | None:
    """The chunk's citation view, or ``None`` if it is out of scope/not found.

    Only ``ACTIVE`` chunks are visible by default. A ``SUPERSEDED`` chunk is
    also visible when ``run_id`` names a run in the caller's org/factory
    whose stored agent results actually cite that chunk id (never a blanket
    "any superseded chunk once you know a run").
    """
    statuses = [DocumentVersionStatus.ACTIVE.value]
    if run_id is not None and await _run_cites_chunk(
        session, run_id=run_id, scope=scope, chunk_id=chunk_id
    ):
        statuses.append(DocumentVersionStatus.SUPERSEDED.value)

    stmt = _base_query(scope, statuses=tuple(statuses)).where(Chunk.id == chunk_id)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    return CitationResult(
        chunk_id=row.id,
        document_id=row.document_id,
        document_slug=row.document_slug,
        title=row.title,
        version_no=row.version_no,
        status=row.status,
        page_number=row.page_number,
        section=row.section,
        text=row.text,
    )


async def _run_cites_chunk(
    session: AsyncSession, *, run_id: uuid.UUID, scope: RetrievalScope, chunk_id: uuid.UUID
) -> bool:
    run = await session.get(AnalysisRun, run_id)
    if (
        run is None
        or run.organization_id != scope.organization_id
        or run.factory_id != scope.factory_id
    ):
        return False
    results = (
        await session.scalars(
            select(AgentResultRecord)
            .join(AgentTask, AgentResultRecord.task_id == AgentTask.id)
            .where(AgentTask.run_id == run_id)
        )
    ).all()
    target = str(chunk_id)
    for result in results:
        payload = result.payload if isinstance(result.payload, dict) else {}
        evidence_refs = payload.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            continue
        for evidence in evidence_refs:
            if isinstance(evidence, dict) and evidence.get("chunk_id") == target:
                return True
    return False
