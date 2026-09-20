"""Hybrid retrieval and citations (task-17-brief.md req. 3-4).

Uses the deterministic `HashingEmbedder`: it has no real semantic
understanding (pure feature-hashed bag-of-words), so "vector finds a
paraphrase" is demonstrated with a crafted pair where the lexical query's
`websearch_to_tsquery` AND-of-terms fails to match at all (one query term is
absent from every document) while the vector search still ranks the
word-overlapping document above an unrelated one by cosine distance.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.policy import Principal
from app.db.models import Chunk, Factory, Organization, User
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.pipeline import create_document_upload, process_document_version
from app.retrieval.search import RetrievalScope, search
from app.retrieval.storage import DocumentStorage
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


def _principal(identity: IdentityFixture, *, factory_id: uuid.UUID, role: str) -> Principal:
    user = identity.users["planner@demo.test"]
    return Principal(
        user_id=user.id,
        organization_id=identity.organization.id,
        session_id=uuid.uuid4(),
        csrf_token="test-csrf",
        display_name=user.display_name,
        roles_by_factory={factory_id: frozenset({role})},
    )


async def _upload(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
    *,
    principal: Principal,
    factory_id: uuid.UUID | None,
    slug: str,
    title: str,
    text: str,
    acl_roles: list[str] | None = None,
) -> uuid.UUID:
    version = await create_document_upload(
        db_session,
        principal,
        factory_id,
        title=title,
        doc_type="SOP",
        slug=slug,
        acl_roles=acl_roles or [],
        filename=f"{slug}.md",
        data=f"# Heading\n{text}\n".encode(),
        storage=storage,
    )
    await db_session.commit()
    await process_document_version(session_factory, version.id, embedder=embedder, storage=storage)
    return version.id


@pytest.fixture
def storage(tmp_path) -> DocumentStorage:  # noqa: ANN001
    return DocumentStorage(tmp_path)


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


async def test_lexical_finds_an_exact_rare_term(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn, role="planner")
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=ktn,
        slug="zephyrflux-procedure",
        title="Zephyrflux Procedure",
        text="The zephyrflux calibration step happens before every changeover.",
    )

    scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"planner"})
    )
    results = await search(db_session, scope, "zephyrflux", mode="lexical", embedder=embedder)
    assert len(results) == 1
    assert results[0].lexical_rank == 1
    assert results[0].vector_rank is None
    assert "zephyrflux" in results[0].text.lower()


async def test_vector_finds_word_overlap_lexical_misses(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn, role="planner")
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=ktn,
        slug="shortage-escalation-sop",
        title="Shortage Escalation SOP",
        text=(
            "When a shortage escalation procedure begins, the storekeeper must "
            "contact the supervisor immediately."
        ),
    )
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=ktn,
        slug="packing-checklist",
        title="Packing Checklist",
        text="Packing and shipment readiness checklist for outbound cartons and labels.",
    )

    scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"planner"})
    )
    query = "shortage escalation notification procedure"  # "notification" is in neither doc

    lexical = await search(db_session, scope, query, mode="lexical", embedder=embedder)
    assert lexical == []  # websearch_to_tsquery ANDs every term; none matches

    vector = await search(db_session, scope, query, mode="vector", embedder=embedder)
    assert vector
    assert vector[0].title == "Shortage Escalation SOP"
    assert vector[0].lexical_rank is None
    assert vector[0].vector_rank == 1


async def test_hybrid_merges_lexical_and_vector_candidates(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn, role="planner")
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=ktn,
        slug="hybrid-target-sop",
        title="Hybrid Target SOP",
        text="Widget torque calibration widget torque calibration must be logged every shift.",
    )

    scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"planner"})
    )
    hybrid = await search(
        db_session, scope, "widget torque calibration", mode="hybrid", embedder=embedder
    )
    assert hybrid
    top = hybrid[0]
    assert top.lexical_rank == 1
    assert top.vector_rank == 1
    assert top.score > 0


async def test_ktn_user_does_not_see_byg_scoped_document(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    byg = identity.factories["BYG"].id
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=byg, role="planner")
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=byg,
        slug="shift-calendar-and-breaks-test",
        title="Shift Calendar and Breaks (Biyagama)",
        text="Biyagama runs two shifts per working day, shift code A and shift code B.",
    )

    scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"planner"})
    )
    results = await search(db_session, scope, "shift code", mode="hybrid", embedder=embedder)
    assert results == []


async def test_acl_hides_document_from_planner_but_not_supervisor(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    uploader = _principal(identity, factory_id=ktn, role="supervisor")
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=uploader,
        factory_id=None,
        slug="worker-data-privacy-search-test",
        title="Worker Data Privacy",
        text="Operator alias records are pseudonymous and restricted to authorized roles.",
        acl_roles=["org_admin", "supervisor", "ie_engineer"],
    )

    planner_scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"planner"})
    )
    supervisor_scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"supervisor"})
    )
    query = "operator alias records pseudonymous"

    assert await search(db_session, planner_scope, query, mode="hybrid", embedder=embedder) == []
    supervisor_results = await search(
        db_session, supervisor_scope, query, mode="hybrid", embedder=embedder
    )
    assert supervisor_results
    assert supervisor_results[0].title == "Worker Data Privacy"


async def test_another_organizations_chunks_never_appear(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn, role="planner")
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=ktn,
        slug="cross-org-term-sop",
        title="Cross Org Term SOP",
        text="The quixotropic fastener torque limit is 4.2 newton-meters.",
    )

    other_org = Organization(name="Other Apparel Co", slug="other-apparel-co")
    db_session.add(other_org)
    await db_session.flush()
    other_factory = Factory(organization_id=other_org.id, code="OTF", name="Other Factory")
    other_user = User(
        issuer="test-issuer", subject="other-user", email="other@other.test", display_name="Other"
    )
    db_session.add_all([other_factory, other_user])
    await db_session.flush()
    other_principal = Principal(
        user_id=other_user.id,
        organization_id=other_org.id,
        session_id=uuid.uuid4(),
        csrf_token="test-csrf",
        display_name="Other User",
        roles_by_factory={other_factory.id: frozenset({"planner"})},
    )
    await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=other_principal,
        factory_id=other_factory.id,
        slug="cross-org-term-sop-other",
        title="Other Org Term SOP",
        text="The quixotropic fastener torque limit is 4.2 newton-meters.",
    )

    scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"planner"})
    )
    results = await search(
        db_session, scope, "quixotropic fastener torque", mode="hybrid", embedder=embedder
    )
    assert len(results) == 1
    assert results[0].title == "Cross Org Term SOP"


async def test_citations_endpoint_enforces_the_same_scope_rules(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
    client: AsyncClient,
) -> None:
    byg = identity.factories["BYG"].id
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=byg, role="planner")
    version_id = await _upload(
        db_session,
        session_factory,
        storage,
        embedder,
        principal=principal,
        factory_id=byg,
        slug="shift-calendar-citation-test",
        title="Shift Calendar Citation Test",
        text="Biyagama shift codes are documented for the citation test.",
    )
    chunk_id = await db_session.scalar(
        select(Chunk.id).where(Chunk.document_version_id == version_id).limit(1)
    )
    assert chunk_id is not None

    ktn_user = await login_as(client, session_factory, "planner@demo.test")
    denied = await ktn_user.get(f"/api/v1/citations/{chunk_id}?factory_id={ktn}")
    assert denied.status_code == 404, denied.text

    byg_user = await login_as(client, session_factory, "byg.planner@demo.test")
    allowed = await byg_user.get(f"/api/v1/citations/{chunk_id}?factory_id={byg}")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["document"]["title"] == "Shift Calendar Citation Test"
