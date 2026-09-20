"""Integration tests for `app.api.notes` (task-18-brief.md requirement 3)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Note
from app.nlp.classifier import get_default_classifier_version
from tests.factories import make_line, make_material, make_order
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

XSS_TEXT = "Line 1 flagged <script>alert('x')</script> in the note, do not run it."


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def test_create_note_classifies_extracts_and_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    order = await make_order(
        db_session, organization=identity.organization, factory=ktn, external_ref="PO-KTN-0099"
    )
    material = await make_material(
        db_session, organization=identity.organization, code="MAT-01", name="Sample Fabric"
    )
    await db_session.commit()

    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    text = (
        f"{order.external_ref} needs {material.name} before Friday; "
        "unrelated PO-KTN-9999 is not a real order."
    )
    headers = {"Idempotency-Key": "note-key-0001"}
    first = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/notes", json={"text": text}, headers=headers
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["text"] == text
    assert body["classification"] in {"planning", "materials", "ie", "quality", "unknown"}
    assert body["classifier_version"] == get_default_classifier_version()
    assert body["notice"] == (
        "Entity links are for navigation only; they do not authorize any action."
    )
    resolved_labels = {(e["label"], e["resolved_id"]) for e in body["entities"]}
    assert (("ORDER", str(order.id))) in resolved_labels
    assert (("MATERIAL", str(material.id))) in resolved_labels
    assert body["unresolved"] and body["unresolved"][0]["text"] == "PO-KTN-9999"
    assert all(e["resolved_id"] is None for e in body["unresolved"])

    async with session_factory() as check:
        stored = (await check.scalars(select(Note).where(Note.factory_id == ktn.id))).all()
        assert len(stored) == 1
        stored_labels = {(e["label"], e["resolved_id"]) for e in stored[0].entities}
        assert stored_labels == resolved_labels

    replay = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/notes", json={"text": text}, headers=headers
    )
    assert replay.status_code == 201
    assert replay.json() == body

    async with session_factory() as check:
        count = (await check.scalars(select(Note.id).where(Note.factory_id == ktn.id))).all()
        assert len(count) == 1


async def test_xss_text_is_stored_and_returned_verbatim(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.post(
        f"/api/v1/factories/{ktn.id}/notes",
        json={"text": XSS_TEXT},
        headers={"Idempotency-Key": "note-xss-0001"},
    )
    assert response.status_code == 201
    assert response.json()["text"] == XSS_TEXT


async def test_viewer_cannot_create_a_note(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    viewer = await login_as(client, session_factory, "viewer@demo.test")
    response = await viewer.post(
        f"/api/v1/factories/{ktn.id}/notes",
        json={"text": "Routine day, no incidents to report."},
        headers={"Idempotency-Key": "note-viewer-0001"},
    )
    assert response.status_code == 403


async def test_cross_factory_note_create_is_denied(
    app,  # noqa: ANN001
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as second_client:
        byg_planner = await login_as(second_client, session_factory, "byg.planner@demo.test")
        response = await byg_planner.post(
            f"/api/v1/factories/{ktn.id}/notes",
            json={"text": "Cross factory note attempt."},
            headers={"Idempotency-Key": "note-cross-0001"},
        )
        assert response.status_code in (403, 404)


async def test_line_entities_do_not_cross_factories(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    byg = identity.factories["BYG"]
    await make_line(
        db_session, organization=identity.organization, factory=byg, code="B1", name="Line B1"
    )
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.post(
        f"/api/v1/factories/{ktn.id}/notes",
        json={"text": "Line B1 picked up the overflow units."},
        headers={"Idempotency-Key": "note-byg-line-0001"},
    )
    assert response.status_code == 201
    assert response.json()["entities"] == []


async def test_list_notes_filters_by_classification_and_paginates(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner = await login_as(client, session_factory, "planner@demo.test")

    texts = [
        "Loading board clean for the week.",
        "FINAL inspection passed with no defects noted for the shift.",
    ]
    for index, text in enumerate(texts):
        response = await planner.post(
            f"/api/v1/factories/{ktn.id}/notes",
            json={"text": text},
            headers={"Idempotency-Key": f"note-list-{index:04d}"},
        )
        assert response.status_code == 201

    listing = await planner.get(f"/api/v1/factories/{ktn.id}/notes")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 2

    quality_only = await planner.get(
        f"/api/v1/factories/{ktn.id}/notes", params={"classification": "quality"}
    )
    assert quality_only.status_code == 200
    quality_payload = quality_only.json()
    assert quality_payload["total"] == 1
    assert quality_payload["items"][0]["classification"] == "quality"
    assert quality_payload["items"][0]["unresolved"] == []


async def test_note_text_length_is_validated(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner = await login_as(client, session_factory, "planner@demo.test")
    too_short = await planner.post(
        f"/api/v1/factories/{ktn.id}/notes",
        json={"text": "hi"},
        headers={"Idempotency-Key": "note-short-0001"},
    )
    assert too_short.status_code == 422

    too_long = await planner.post(
        f"/api/v1/factories/{ktn.id}/notes",
        json={"text": "x" * 2001},
        headers={"Idempotency-Key": "note-long-0001"},
    )
    assert too_long.status_code == 422


async def test_unknown_factory_returns_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.post(
        f"/api/v1/factories/{uuid.uuid4()}/notes",
        json={"text": "Note for a factory that does not exist."},
        headers={"Idempotency-Key": "note-missing-0001"},
    )
    assert response.status_code == 404
