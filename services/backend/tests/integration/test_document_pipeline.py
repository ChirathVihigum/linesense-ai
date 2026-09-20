"""Upload intake and background processing (task-17-brief.md req. 1-2).

Uses `HashingEmbedder` and a `tmp_path` storage dir throughout, per the
task's heat policy (never fastembed in tests).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.auth.policy import Principal
from app.db.models.documents import Chunk, DocumentVersion
from app.domain.vocab import DocumentVersionStatus
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.pipeline import create_document_upload, process_document_version
from app.retrieval.search import RetrievalScope, search
from app.retrieval.storage import DocumentStorage
from app.settings import Settings, resolve_backend_path
from tests.helpers.auth import IdentityFixture, login_as, seed_identity
from tests.helpers.pdf import make_encrypted_pdf

pytestmark = pytest.mark.integration

MAX_UPLOAD_BYTES = 10_485_760


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


@pytest.fixture
def storage(tmp_path: Path) -> DocumentStorage:
    return DocumentStorage(tmp_path)


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


def _principal(
    identity: IdentityFixture, *, factory_id: uuid.UUID, role: str = "supervisor"
) -> Principal:
    user = identity.users["supervisor@demo.test"]
    return Principal(
        user_id=user.id,
        organization_id=identity.organization.id,
        session_id=uuid.uuid4(),
        csrf_token="test-csrf",
        display_name=user.display_name,
        roles_by_factory={factory_id: frozenset({role})},
    )


async def test_upload_and_process_activates_with_chunks_and_embeddings(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn)

    version = await create_document_upload(
        db_session,
        principal,
        ktn,
        title="Pipeline Test SOP",
        doc_type="SOP",
        slug="pipeline-test-sop",
        acl_roles=[],
        filename="doc.md",
        data=b"# Purpose\nA short procedure body with enough words to form one chunk.\n",
        storage=storage,
    )
    await db_session.commit()
    assert version.status == DocumentVersionStatus.QUARANTINE.value
    assert (storage.quarantine_dir / version.storage_key).exists()

    await process_document_version(session_factory, version.id, embedder=embedder, storage=storage)

    async with session_factory() as session:
        refreshed = await session.get(DocumentVersion, version.id)
        assert refreshed is not None
        assert refreshed.status == DocumentVersionStatus.ACTIVE.value
        assert refreshed.activated_at is not None
        chunks = (
            await session.scalars(select(Chunk).where(Chunk.document_version_id == version.id))
        ).all()
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.embedding is not None
            assert len(chunk.embedding) == 384
            assert chunk.embedding_model == "hashing-384-test"

    assert not (storage.quarantine_dir / version.storage_key).exists()
    assert (storage.store_dir / version.storage_key).exists()


async def test_reupload_identical_content_conflicts(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn)
    data = b"# Purpose\nIdentical content used twice.\n"

    first = await create_document_upload(
        db_session,
        principal,
        ktn,
        title="Duplicate Test SOP",
        doc_type="SOP",
        slug="duplicate-test-sop",
        acl_roles=[],
        filename="doc.md",
        data=data,
        storage=storage,
    )
    await db_session.commit()
    await process_document_version(session_factory, first.id, embedder=embedder, storage=storage)

    with pytest.raises(AppError) as excinfo:
        await create_document_upload(
            db_session,
            principal,
            ktn,
            title="Duplicate Test SOP",
            doc_type="SOP",
            slug="duplicate-test-sop",
            acl_roles=[],
            filename="doc.md",
            data=data,
            storage=storage,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "CONFLICT"


async def test_second_version_supersedes_the_first_and_v1_is_not_searchable(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn)

    v1 = await create_document_upload(
        db_session,
        principal,
        ktn,
        title="Versioned SOP",
        doc_type="SOP",
        slug="versioned-sop",
        acl_roles=[],
        filename="doc.md",
        data=b"# Purpose\nFirst version mentions the unique term xylospindle here.\n",
        storage=storage,
    )
    await db_session.commit()
    await process_document_version(session_factory, v1.id, embedder=embedder, storage=storage)

    v2 = await create_document_upload(
        db_session,
        principal,
        ktn,
        title="Versioned SOP",
        doc_type="SOP",
        slug="versioned-sop",
        acl_roles=[],
        filename="doc.md",
        data=b"# Purpose\nSecond version has completely different wording altogether.\n",
        storage=storage,
    )
    await db_session.commit()
    assert v2.version_no == v1.version_no + 1
    await process_document_version(session_factory, v2.id, embedder=embedder, storage=storage)

    async with session_factory() as session:
        v1_refreshed = await session.get(DocumentVersion, v1.id)
        v2_refreshed = await session.get(DocumentVersion, v2.id)
        assert (
            v1_refreshed is not None
            and v1_refreshed.status == DocumentVersionStatus.SUPERSEDED.value
        )
        assert (
            v2_refreshed is not None and v2_refreshed.status == DocumentVersionStatus.ACTIVE.value
        )

    scope = RetrievalScope(
        organization_id=identity.organization.id, factory_id=ktn, roles=frozenset({"supervisor"})
    )
    results = await search(db_session, scope, "xylospindle", mode="lexical", embedder=embedder)
    assert results == []


async def test_rejected_encrypted_pdf_is_removed_from_quarantine(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn)

    version = await create_document_upload(
        db_session,
        principal,
        ktn,
        title="Encrypted PDF",
        doc_type="OTHER",
        slug="encrypted-pdf-test",
        acl_roles=[],
        filename="doc.pdf",
        data=make_encrypted_pdf(),
        storage=storage,
    )
    await db_session.commit()
    storage_key = version.storage_key
    assert (storage.quarantine_dir / storage_key).exists()

    await process_document_version(session_factory, version.id, embedder=embedder, storage=storage)

    async with session_factory() as session:
        refreshed = await session.get(DocumentVersion, version.id)
        assert refreshed is not None
        assert refreshed.status == DocumentVersionStatus.REJECTED.value
        assert refreshed.rejection_reason == "Encrypted PDFs are not supported."

    assert not (storage.quarantine_dir / storage_key).exists()
    assert not (storage.store_dir / storage_key).exists()


async def test_lease_lost_reprocessing_does_not_double_insert_chunks(
    identity: IdentityFixture,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    embedder: HashingEmbedder,
) -> None:
    ktn = identity.factories["KTN"].id
    principal = _principal(identity, factory_id=ktn)

    version = await create_document_upload(
        db_session,
        principal,
        ktn,
        title="Lease Lost SOP",
        doc_type="SOP",
        slug="lease-lost-sop",
        acl_roles=[],
        filename="doc.md",
        data=b"# Purpose\nContent chunked once regardless of how many times processing retries.\n",
        storage=storage,
    )
    await db_session.commit()

    # First attempt runs to completion (ACTIVE); a retried delivery of the
    # same `document.process` job (e.g. after a lease was lost mid-run)
    # calls this again with the same version id.
    await process_document_version(session_factory, version.id, embedder=embedder, storage=storage)
    await process_document_version(session_factory, version.id, embedder=embedder, storage=storage)

    async with session_factory() as session:
        chunk_count = await session.scalar(
            select(sa.func.count())
            .select_from(Chunk)
            .where(Chunk.document_version_id == version.id)
        )
        assert chunk_count == 1
        refreshed = await session.get(DocumentVersion, version.id)
        assert refreshed is not None
        assert refreshed.status == DocumentVersionStatus.ACTIVE.value


async def test_oversize_upload_is_rejected(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    ktn = identity.factories["KTN"].id
    oversized = b"a" * (MAX_UPLOAD_BYTES + 1)

    response = await supervisor.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Oversize",
            "doc_type": "SOP",
            "slug": "oversize-sop",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("big.md", oversized, "text/markdown")},
        headers={"Idempotency-Key": "upload-oversize-1"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_pdf_extension_with_markdown_content_is_unsupported_media_type(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    ktn = identity.factories["KTN"].id

    response = await supervisor.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Fake PDF",
            "doc_type": "SOP",
            "slug": "fake-pdf-sop",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("doc.pdf", b"# Not actually a PDF\n", "application/pdf")},
        headers={"Idempotency-Key": "upload-fake-pdf-1"},
    )
    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_malicious_filename_never_affects_the_storage_path(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    ktn = identity.factories["KTN"].id

    response = await supervisor.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Path Traversal Attempt",
            "doc_type": "SOP",
            "slug": "path-traversal-sop",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("../../etc/passwd.md", b"# Heading\nHarmless content.\n", "text/markdown")},
        headers={"Idempotency-Key": "upload-traversal-1"},
    )
    assert response.status_code == 202, response.text
    version_id = response.json()["version_id"]

    async with session_factory() as session:
        version = await session.get(DocumentVersion, uuid.UUID(version_id))
        assert version is not None
        # The storage key is a plain uuid4 hex, never derived from the filename.
        assert uuid.UUID(version.storage_key)
        assert "/" not in version.storage_key and ".." not in version.storage_key

    storage_root = resolve_backend_path(settings.document_storage_dir)
    quarantine_dir = storage_root / "quarantine"
    stored_names = {p.name for p in quarantine_dir.iterdir()} if quarantine_dir.exists() else set()
    assert version.storage_key in stored_names
    assert not (storage_root / "etc").exists()
