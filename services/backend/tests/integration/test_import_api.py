"""Integration tests for `app.api.imports` (task-7-brief.md)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BomVersion, Customer, Order, Style
from tests.factories import make_order
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

HEADER = "external_ref,customer_code,style_code,quantity,due_date,priority"


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _make_ready_style(session: AsyncSession, organization_id: uuid.UUID) -> Style:
    suffix = uuid.uuid4().hex[:8]
    style = Style(
        organization_id=organization_id,
        code=f"STY-{suffix}",
        name=f"Style {suffix}",
        product_type="knit-top",
    )
    session.add(style)
    await session.flush()
    session.add(BomVersion(style_id=style.id, version_no=1, is_active=True))
    await session.flush()
    return style


async def _make_customer(session: AsyncSession, organization_id: uuid.UUID) -> Customer:
    suffix = uuid.uuid4().hex[:8]
    customer = Customer(
        organization_id=organization_id, code=f"CUST-{suffix}", name=f"Customer {suffix}"
    )
    session.add(customer)
    await session.flush()
    return customer


def _csv_bytes(*rows: str) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("utf-8")


async def test_validate_then_commit_creates_orders(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    await db_session.commit()
    ktn = identity.factories["KTN"]

    planner = await login_as(client, session_factory, "planner@demo.test")

    raw = _csv_bytes(
        f"PO-IMP-1,{customer.code},{style.code},100,2099-01-01,3",
        f"PO-IMP-2,{customer.code},{style.code},200,2099-02-01,1",
    )
    upload = await planner.post(
        f"/api/v1/factories/{ktn.id}/imports/orders",
        files={"file": ("orders.csv", raw, "text/csv")},
        headers={"Idempotency-Key": "import-key-1"},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["status"] == "VALIDATED"
    assert body["row_count"] == 2
    assert body["errors"] == []
    assert len(body["preview"]) == 2
    batch_id = body["batch_id"]

    fetched = await planner.get(f"/api/v1/imports/{batch_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "VALIDATED"

    commit = await planner.post(
        f"/api/v1/imports/{batch_id}/commit", headers={"Idempotency-Key": "import-commit-1"}
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["status"] == "COMMITTED"

    async with session_factory() as check:
        rows = (
            await check.scalars(
                select(Order).where(Order.external_ref.in_(["PO-IMP-1", "PO-IMP-2"]))
            )
        ).all()
    assert len(rows) == 2
    assert {row.source for row in rows} == {"csv_import"}


async def test_commit_fails_when_row_becomes_invalid_meanwhile(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    await db_session.commit()
    ktn = identity.factories["KTN"]

    planner = await login_as(client, session_factory, "planner@demo.test")

    raw = _csv_bytes(f"PO-CONFLICT,{customer.code},{style.code},50,2099-01-01,3")
    upload = await planner.post(
        f"/api/v1/factories/{ktn.id}/imports/orders",
        files={"file": ("orders.csv", raw, "text/csv")},
        headers={"Idempotency-Key": "import-key-conflict"},
    )
    assert upload.status_code == 201
    batch_id = upload.json()["batch_id"]

    # A conflicting order is created directly (bypassing the API) between validate and commit.
    await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-CONFLICT",
    )
    await db_session.commit()

    commit = await planner.post(
        f"/api/v1/imports/{batch_id}/commit", headers={"Idempotency-Key": "import-commit-conflict"}
    )
    assert commit.status_code == 409
    assert commit.json()["error"]["code"] == "CONFLICT"

    async with session_factory() as check:
        rows = (
            await check.scalars(select(Order.id).where(Order.external_ref == "PO-CONFLICT"))
        ).all()
    assert len(rows) == 1  # only the row created directly above; nothing from the import


async def test_same_file_committed_twice_conflicts(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    await db_session.commit()
    ktn = identity.factories["KTN"]

    planner = await login_as(client, session_factory, "planner@demo.test")
    raw = _csv_bytes(f"PO-TWICE,{customer.code},{style.code},50,2099-01-01,3")

    first_upload = await planner.post(
        f"/api/v1/factories/{ktn.id}/imports/orders",
        files={"file": ("orders.csv", raw, "text/csv")},
        headers={"Idempotency-Key": "import-key-twice-1"},
    )
    batch_id = first_upload.json()["batch_id"]
    first_commit = await planner.post(
        f"/api/v1/imports/{batch_id}/commit", headers={"Idempotency-Key": "import-commit-twice-1"}
    )
    assert first_commit.status_code == 200

    second_upload = await planner.post(
        f"/api/v1/factories/{ktn.id}/imports/orders",
        files={"file": ("orders.csv", raw, "text/csv")},
        headers={"Idempotency-Key": "import-key-twice-2"},
    )
    assert second_upload.status_code == 409
    assert second_upload.json()["error"]["code"] == "CONFLICT"


async def test_viewer_cannot_upload(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    await db_session.commit()
    ktn = identity.factories["KTN"]

    viewer = await login_as(client, session_factory, "viewer@demo.test")
    raw = _csv_bytes(f"PO-VIEW,{customer.code},{style.code},50,2099-01-01,3")
    response = await viewer.post(
        f"/api/v1/factories/{ktn.id}/imports/orders",
        files={"file": ("orders.csv", raw, "text/csv")},
        headers={"Idempotency-Key": "import-key-viewer"},
    )
    assert response.status_code == 403


async def test_csv_template_endpoint(client: AsyncClient) -> None:
    response = await client.get("/api/v1/imports/templates/orders.csv")
    assert response.status_code == 200
    assert response.text.splitlines()[0] == HEADER
