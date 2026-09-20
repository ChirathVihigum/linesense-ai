"""HTTP-level access control for document upload/list/detail/download
(backend-contracts.md section 4; task-17-brief.md req. 1, 4).

Search/citation scope and ACL enforcement are covered by
`tests/integration/test_search.py`; this file covers the document routes
themselves: who may upload, who may see a document at all, and the same
checks re-applied on download.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent
from app.domain.vocab import AuditOutcome
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


def _md(text: str) -> bytes:
    return f"# Heading\n{text}\n".encode()


def _other_client(client: AsyncClient) -> AsyncClient:
    """A second client sharing the ASGI transport but not the cookie jar
    (`login_as` warns that one `AsyncClient` holds only one identity at a time)."""
    return AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001


async def test_planner_cannot_upload_a_document(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await login_as(client, session_factory, "planner@demo.test")
    ktn = identity.factories["KTN"].id

    response = await planner.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Planner Attempt",
            "doc_type": "SOP",
            "slug": "planner-attempt",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("doc.md", _md("Content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-planner-1"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"

    async with session_factory() as session:
        denials = (
            await session.scalars(
                sa.select(AuditEvent).where(
                    AuditEvent.action == "document.upload",
                    AuditEvent.outcome == AuditOutcome.DENIED.value,
                )
            )
        ).all()
        assert len(denials) == 1


async def test_org_scope_upload_requires_org_admin_or_supervisor(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    quality = await login_as(client, session_factory, "quality@demo.test")
    ktn = identity.factories["KTN"].id

    response = await quality.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Quality Org Attempt",
            "doc_type": "QUALITY_POLICY",
            "slug": "quality-org-attempt",
            "scope": "org",
            "acl_roles": "",
        },
        files={"file": ("doc.md", _md("Content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-quality-org-1"},
    )
    assert response.status_code == 403, response.text

    # But a factory-scoped upload by the same role is allowed.
    ok = await quality.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Quality Factory Doc",
            "doc_type": "QUALITY_POLICY",
            "slug": "quality-factory-doc",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("doc.md", _md("Content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-quality-factory-1"},
    )
    assert ok.status_code == 202, ok.text


async def test_document_list_hides_other_factory_scoped_documents(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    ktn = identity.factories["KTN"].id
    byg = identity.factories["BYG"].id

    await supervisor.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "KTN Doc",
            "doc_type": "SOP",
            "slug": "ktn-visibility-doc",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("doc.md", _md("KTN content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-ktn-vis-1"},
    )

    byg_planner = await login_as(_other_client(client), session_factory, "byg.planner@demo.test")
    await byg_planner.post(
        f"/api/v1/factories/{byg}/documents",
        data={
            "title": "BYG Doc",
            "doc_type": "SOP",
            "slug": "byg-visibility-doc",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("doc.md", _md("BYG content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-byg-vis-1"},
    )

    ktn_list = await supervisor.get(f"/api/v1/factories/{ktn}/documents")
    assert ktn_list.status_code == 200, ktn_list.text
    slugs = {item["slug"] for item in ktn_list.json()["items"]}
    assert "ktn-visibility-doc" in slugs
    assert "byg-visibility-doc" not in slugs


async def test_acl_restricted_document_is_404_for_planner_but_visible_to_supervisor(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    ktn = identity.factories["KTN"].id

    upload = await supervisor.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Worker Data Privacy",
            "doc_type": "OTHER",
            "slug": "worker-data-privacy-acl-test",
            "scope": "org",
            "acl_roles": "org_admin,supervisor,ie_engineer",
        },
        files={"file": ("doc.md", _md("Restricted content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-acl-1"},
    )
    assert upload.status_code == 202, upload.text
    document_id = upload.json()["document_id"]

    planner = await login_as(_other_client(client), session_factory, "planner@demo.test")
    denied = await planner.get(f"/api/v1/documents/{document_id}")
    assert denied.status_code == 404, denied.text

    allowed = await supervisor.get(f"/api/v1/documents/{document_id}")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["slug"] == "worker-data-privacy-acl-test"


async def test_download_requires_an_activated_version(
    identity: IdentityFixture,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    ktn = identity.factories["KTN"].id

    upload = await supervisor.post(
        f"/api/v1/factories/{ktn}/documents",
        data={
            "title": "Download Test Doc",
            "doc_type": "SOP",
            "slug": "download-test-doc",
            "scope": "factory",
            "acl_roles": "",
        },
        files={"file": ("doc.md", _md("Download content"), "text/markdown")},
        headers={"Idempotency-Key": "upload-download-1"},
    )
    assert upload.status_code == 202, upload.text
    version_id = upload.json()["version_id"]

    # The background job has not run yet: still QUARANTINE, so download 404s
    # rather than ever streaming a not-yet-scanned file.
    still_quarantined = await supervisor.get(f"/api/v1/document-versions/{version_id}/download")
    assert still_quarantined.status_code == 404, still_quarantined.text
