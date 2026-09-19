"""Integration tests for the inventory ledger, reservations and material
overview (task-8-brief.md)."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.db.models import (
    AuditEvent,
    ExpectedReceipt,
    MaterialBalance,
    MaterialLot,
    Order,
    Reservation,
    StockMovement,
)
from app.domain.clock import utcnow
from app.domain.inventory import service as inventory_service
from app.domain.vocab import ProductionState
from tests.factories import make_material
from tests.helpers.auth import IdentityFixture, login_as, seed_identity
from tests.helpers.inventory import grant_role, make_material_order, storekeeper_principal

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"key-{uuid.uuid4().hex}"}


async def _balance(
    session_factory: async_sessionmaker[AsyncSession], factory_id: uuid.UUID, material_id: Any
) -> MaterialBalance:
    async with session_factory() as check:
        balance = await check.scalar(
            select(MaterialBalance).where(
                MaterialBalance.factory_id == factory_id,
                MaterialBalance.material_id == material_id,
            )
        )
    assert balance is not None
    return balance


async def test_receipt_into_accepted_lot_updates_balance_and_version(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material = await make_material(db_session, organization=identity.organization)
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")

    url = f"/api/v1/factories/{ktn.id}/stock/receipts"
    payload = {
        "material_id": str(material.id),
        "lot_code": "LOT-A1",
        "quantity": "100",
        "accept": True,
    }
    headers = _key()
    first = await storekeeper.post(url, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["movement"]["movement_type"] == "RECEIPT"
    assert D(body["movement"]["quantity"]) == D(100)
    assert body["lot"]["status"] == "ACCEPTED"
    assert D(body["balance"]["on_hand_accepted"]) == D(100)
    # Created at version 1, then incremented by the receipt.
    assert body["balance"]["version"] == 2

    replay = await storekeeper.post(url, json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == body

    second = await storekeeper.post(
        url, json={**payload, "quantity": "50", "accept": False}, headers=_key()
    )
    assert second.status_code == 201, second.text
    assert D(second.json()["balance"]["on_hand_accepted"]) == D(150)
    assert second.json()["balance"]["version"] == 3

    balance = await _balance(session_factory, ktn.id, material.id)
    assert balance.on_hand_accepted == D(150)
    assert balance.version == 3

    async with session_factory() as check:
        movements = (
            await check.scalars(
                select(StockMovement).where(StockMovement.material_id == material.id)
            )
        ).all()
        audit = (
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.receipt"))
        ).all()
    assert len(movements) == 2
    assert len(audit) == 2


async def test_quarantine_receipt_counts_only_after_acceptance(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material = await make_material(db_session, organization=identity.organization)
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")

    receipt = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/receipts",
        json={
            "material_id": str(material.id),
            "lot_code": "LOT-Q1",
            "quantity": "80",
            "accept": False,
        },
        headers=_key(),
    )
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["lot"]["status"] == "QUARANTINE"
    assert D(receipt.json()["balance"]["on_hand_accepted"]) == D(0)
    lot_id = receipt.json()["lot"]["id"]

    # A second quarantined receipt into the same lot.
    again = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/receipts",
        json={
            "material_id": str(material.id),
            "lot_code": "LOT-Q1",
            "quantity": "20",
            "accept": False,
        },
        headers=_key(),
    )
    assert again.status_code == 201
    assert D(again.json()["balance"]["on_hand_accepted"]) == D(0)

    accepted = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/lots/{lot_id}/accept", headers=_key()
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["lot"]["status"] == "ACCEPTED"
    assert D(accepted.json()["balance"]["on_hand_accepted"]) == D(100)

    twice = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/lots/{lot_id}/accept", headers=_key()
    )
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "CONFLICT"


async def _receive(
    storekeeper: Any, factory_id: uuid.UUID, material_id: uuid.UUID, qty: str, lot: str
) -> dict[str, Any]:
    response = await storekeeper.post(
        f"/api/v1/factories/{factory_id}/stock/receipts",
        json={"material_id": str(material_id), "lot_code": lot, "quantity": qty, "accept": True},
        headers=_key(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_issue_beyond_available_conflicts_and_consumes_own_reservation(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material, order = await make_material_order(
        db_session, identity.organization, ktn, quantity=1000
    )
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    receipt = await _receive(storekeeper, ktn.id, material.id, "100", "LOT-I1")
    lot_id = receipt["lot"]["id"]

    reserve = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/reservations",
        json={"material_id": str(material.id), "order_id": str(order.id), "quantity": "50"},
        headers=_key(),
    )
    assert reserve.status_code == 201, reserve.text
    reservation_id = reserve.json()["reservation"]["id"]
    assert D(reserve.json()["balance"]["available_now"]) == D(50)

    issue_url = f"/api/v1/factories/{ktn.id}/stock/issues"
    too_much = await storekeeper.post(
        issue_url,
        json={"material_id": str(material.id), "lot_id": lot_id, "quantity": "60"},
        headers=_key(),
    )
    assert too_much.status_code == 409
    assert too_much.json()["error"]["code"] == "CONFLICT"

    # The order's own reservation may be consumed: partially (20 of 50).
    partial = await storekeeper.post(
        issue_url,
        json={
            "material_id": str(material.id),
            "lot_id": lot_id,
            "quantity": "20",
            "order_id": str(order.id),
            "reason": "cutting",
        },
        headers=_key(),
    )
    assert partial.status_code == 201, partial.text
    assert D(partial.json()["movement"]["quantity"]) == D(-20)
    assert D(partial.json()["balance"]["on_hand_accepted"]) == D(80)
    assert D(partial.json()["balance"]["reserved"]) == D(30)

    async with session_factory() as check:
        rows = (
            await check.scalars(
                select(Reservation)
                .where(Reservation.order_id == order.id)
                .order_by(Reservation.status)
            )
        ).all()
    by_status = {(r.status, r.quantity) for r in rows}
    assert by_status == {("ACTIVE", D(30)), ("CONSUMED", D(20))}
    assert any(r.id == uuid.UUID(reservation_id) and r.status == "ACTIVE" for r in rows)

    # 80 on hand, 30 reserved for the order: issuing 70 for the order consumes
    # the remaining 30 and leaves 10 on hand, 0 reserved.
    full = await storekeeper.post(
        issue_url,
        json={
            "material_id": str(material.id),
            "lot_id": lot_id,
            "quantity": "70",
            "order_id": str(order.id),
        },
        headers=_key(),
    )
    assert full.status_code == 201, full.text
    balance = full.json()["balance"]
    assert D(balance["on_hand_accepted"]) == D(10)
    assert D(balance["reserved"]) == D(0)

    lot_overdraw = await storekeeper.post(
        issue_url,
        json={"material_id": str(material.id), "lot_id": lot_id, "quantity": "11"},
        headers=_key(),
    )
    assert lot_overdraw.status_code == 409


async def test_correction_references_original_and_respects_reserved(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material, order = await make_material_order(db_session, identity.organization, ktn)
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    receipt = await _receive(storekeeper, ktn.id, material.id, "100", "LOT-C1")
    movement_id = receipt["movement"]["id"]

    reserve = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/reservations",
        json={"material_id": str(material.id), "order_id": str(order.id), "quantity": "30"},
        headers=_key(),
    )
    assert reserve.status_code == 201

    url = f"/api/v1/factories/{ktn.id}/stock/corrections"
    below_reserved = await storekeeper.post(
        url,
        json={"movement_id": movement_id, "quantity_delta": "-80", "reason": "miscount"},
        headers=_key(),
    )
    assert below_reserved.status_code == 409

    missing_reason = await storekeeper.post(
        url, json={"movement_id": movement_id, "quantity_delta": "-5"}, headers=_key()
    )
    assert missing_reason.status_code == 422

    ok = await storekeeper.post(
        url,
        json={"movement_id": movement_id, "quantity_delta": "-50", "reason": "miscount"},
        headers=_key(),
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["movement"]["movement_type"] == "CORRECTION"
    assert body["movement"]["corrects_movement_id"] == movement_id
    assert body["movement"]["reason"] == "miscount"
    assert D(body["balance"]["on_hand_accepted"]) == D(50)
    assert D(body["balance"]["reserved"]) == D(30)

    unknown = await storekeeper.post(
        url,
        json={"movement_id": str(uuid.uuid4()), "quantity_delta": "5", "reason": "x" * 5},
        headers=_key(),
    )
    assert unknown.status_code == 404


async def test_ledger_reservations_and_release_routes(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material, order = await make_material_order(db_session, identity.organization, ktn)
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    for index in range(3):
        await _receive(storekeeper, ktn.id, material.id, "10", f"LOT-L{index}")

    ledger = await storekeeper.get(
        f"/api/v1/factories/{ktn.id}/materials/{material.id}/ledger",
        params={"limit": 2, "offset": 0},
    )
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["total"] == 3
    assert len(ledger.json()["items"]) == 2
    assert ledger.json()["items"][0]["lot_code"] == "LOT-L2"

    missing = await storekeeper.get(f"/api/v1/factories/{ktn.id}/materials/{uuid.uuid4()}/ledger")
    assert missing.status_code == 404

    reserve = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/reservations",
        json={"material_id": str(material.id), "order_id": str(order.id), "quantity": "12.5"},
        headers=_key(),
    )
    assert reserve.status_code == 201, reserve.text
    reservation_id = reserve.json()["reservation"]["id"]

    too_much = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/reservations",
        json={"material_id": str(material.id), "order_id": str(order.id), "quantity": "20"},
        headers=_key(),
    )
    assert too_much.status_code == 409
    assert too_much.json()["error"]["message"] == "Insufficient available material"

    listed = await storekeeper.get(
        f"/api/v1/factories/{ktn.id}/reservations", params={"order_id": str(order.id)}
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == reservation_id

    release = await storekeeper.post(
        f"/api/v1/reservations/{reservation_id}/release", headers=_key()
    )
    assert release.status_code == 200, release.text
    assert release.json()["reservation"]["status"] == "RELEASED"
    assert D(release.json()["balance"]["reserved"]) == D(0)

    again = await storekeeper.post(f"/api/v1/reservations/{reservation_id}/release", headers=_key())
    assert again.status_code == 409


async def test_material_overview(
    client: AsyncClient,
    app: Any,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    busy = await make_material(
        db_session,
        organization=identity.organization,
        code="MAT-BUSY",
        safety_stock=D(10),
        lead_time_days=5,
    )
    await make_material(db_session, organization=identity.organization, code="MAT-IDLE")
    db_session.add(
        ExpectedReceipt(
            organization_id=identity.organization.id,
            factory_id=ktn.id,
            material_id=busy.id,
            quantity=D(40),
            expected_date=utcnow().date() + timedelta(days=3),
            supplier_ref="SUP-1",
            status="OPEN",
        )
    )
    await db_session.commit()

    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    receipt = await _receive(storekeeper, ktn.id, busy.id, "100", "LOT-O1")
    issue = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/issues",
        json={"material_id": str(busy.id), "lot_id": receipt["lot"]["id"], "quantity": "28"},
        headers=_key(),
    )
    assert issue.status_code == 201, issue.text

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as viewer_client:
        viewer = await login_as(viewer_client, session_factory, "viewer@demo.test")
        response = await viewer.get(f"/api/v1/factories/{ktn.id}/materials")
    assert response.status_code == 200, response.text
    rows = {row["material_code"]: row for row in response.json()["items"]}

    busy_row = rows["MAT-BUSY"]
    assert D(busy_row["on_hand"]) == D(72)
    assert D(busy_row["reserved"]) == D(0)
    assert D(busy_row["available_now"]) == D(72)
    assert D(busy_row["open_receipt_quantity"]) == D(40)
    assert busy_row["next_receipt_date"] is not None
    # 28 issued over a 14-day window = 2 per day; 72 / 2 = 36 days.
    assert D(busy_row["average_daily_consumption"]) == D(2)
    assert D(busy_row["coverage_days"]) == D(36)
    # reorder point = 2 * 5 + 10 = 20
    assert D(busy_row["reorder_point"]) == D(20)
    assert busy_row["below_reorder_point"] is False
    assert busy_row["balance_version"] == 3
    assert busy_row["status_source"] == "Calculated from records"

    idle_row = rows["MAT-IDLE"]
    assert D(idle_row["on_hand"]) == D(0)
    assert idle_row["coverage_days"] is None
    assert idle_row["balance_version"] is None


async def test_viewer_and_planner_cannot_post_receipts(
    client: AsyncClient,
    app: Any,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material = await make_material(db_session, organization=identity.organization)
    await db_session.commit()
    payload = {
        "material_id": str(material.id),
        "lot_code": "LOT-D1",
        "quantity": "5",
        "accept": True,
    }
    for email in ("viewer@demo.test", "planner@demo.test"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as own_client:
            authed = await login_as(own_client, session_factory, email)
            response = await authed.post(
                f"/api/v1/factories/{ktn.id}/stock/receipts", json=payload, headers=_key()
            )
            assert response.status_code == 403, (email, response.text)

    async with session_factory() as check:
        denied = (
            await check.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "inventory.receipt", AuditEvent.outcome == "DENIED"
                )
            )
        ).all()
        movements = await check.scalar(select(sa.func.count()).select_from(StockMovement))
    assert len(denied) == 2
    assert movements == 0


async def test_byg_storekeeper_cannot_touch_ktn_inventory(
    client: AsyncClient,
    app: Any,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    byg = identity.factories["BYG"]
    material, order = await make_material_order(db_session, identity.organization, ktn)
    await db_session.commit()

    ktn_storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    receipt = await _receive(ktn_storekeeper, ktn.id, material.id, "40", "LOT-K1")
    reserve = await ktn_storekeeper.post(
        f"/api/v1/factories/{ktn.id}/reservations",
        json={"material_id": str(material.id), "order_id": str(order.id), "quantity": "10"},
        headers=_key(),
    )
    assert reserve.status_code == 201
    reservation_id = reserve.json()["reservation"]["id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as byg_client:
        byg_storekeeper = await login_as(byg_client, session_factory, "byg.store@example.test")
        await grant_role(
            session_factory,
            user_id=byg_storekeeper.user.id,
            organization_id=identity.organization.id,
            factory_id=byg.id,
            role="storekeeper",
        )
        base = f"/api/v1/factories/{ktn.id}"
        attempts = [
            await byg_storekeeper.post(
                f"{base}/stock/receipts",
                json={
                    "material_id": str(material.id),
                    "lot_code": "LOT-X",
                    "quantity": "1",
                    "accept": True,
                },
                headers=_key(),
            ),
            await byg_storekeeper.post(
                f"{base}/stock/lots/{receipt['lot']['id']}/accept", headers=_key()
            ),
            await byg_storekeeper.post(
                f"{base}/stock/issues",
                json={
                    "material_id": str(material.id),
                    "lot_id": receipt["lot"]["id"],
                    "quantity": "1",
                },
                headers=_key(),
            ),
            await byg_storekeeper.post(
                f"{base}/stock/corrections",
                json={
                    "movement_id": receipt["movement"]["id"],
                    "quantity_delta": "1",
                    "reason": "sneaky",
                },
                headers=_key(),
            ),
            await byg_storekeeper.post(
                f"/api/v1/reservations/{reservation_id}/release", headers=_key()
            ),
            await byg_storekeeper.get(f"{base}/materials"),
            await byg_storekeeper.get(f"{base}/materials/{material.id}/ledger"),
            await byg_storekeeper.get(f"{base}/reservations"),
        ]
        assert [r.status_code for r in attempts] == [404] * len(attempts)

        # The same storekeeper cannot use a KTN lot through its own factory either.
        cross = await byg_storekeeper.post(
            f"/api/v1/factories/{byg.id}/stock/issues",
            json={
                "material_id": str(material.id),
                "lot_id": receipt["lot"]["id"],
                "quantity": "1",
            },
            headers=_key(),
        )
        assert cross.status_code == 422, cross.text

    balance = await _balance(session_factory, ktn.id, material.id)
    assert balance.on_hand_accepted == D(40)
    assert balance.reserved == D(10)


async def test_movements_recompute_material_state(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material, order = await make_material_order(
        db_session,
        identity.organization,
        ktn,
        quantity=100,
        quantity_per_unit=D("1.2"),
        wastage_fraction=D("0.05"),
    )
    _, dispatched = await make_material_order(
        db_session,
        identity.organization,
        ktn,
        material=material,
        production_state=ProductionState.DISPATCHED.value,
    )
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")

    # Demand 100 * 1.2 * 1.05 = 126; 100 on hand -> shortage, no receipts.
    receipt = await _receive(storekeeper, ktn.id, material.id, "100", "LOT-S1")
    async with session_factory() as check:
        refreshed = await check.get(Order, order.id)
        untouched = await check.get(Order, dispatched.id)
    assert refreshed is not None and refreshed.material_state == "SHORTAGE"
    assert refreshed.version == 2
    assert untouched is not None and untouched.material_state == "UNKNOWN"

    await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/corrections",
        json={
            "movement_id": receipt["movement"]["id"],
            "quantity_delta": "26",
            "reason": "recount",
        },
        headers=_key(),
    )
    async with session_factory() as check:
        refreshed = await check.get(Order, order.id)
    assert refreshed is not None and refreshed.material_state == "READY"
    assert refreshed.version == 3


async def test_random_command_sequence_keeps_ledger_equal_to_balance(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material, order_a = await make_material_order(db_session, identity.organization, ktn)
    _, order_b = await make_material_order(
        db_session, identity.organization, ktn, material=material
    )
    await db_session.commit()
    principal = await storekeeper_principal(db_session, identity.organization, ktn)
    orders = [order_a.id, order_b.id]

    rng = random.Random(20260917)  # noqa: S311 - deterministic test data
    lot_codes: list[str] = []
    successes = 0
    conflicts = 0
    for step in range(30):
        async with session_factory() as session:
            lots = list(
                (
                    await session.scalars(
                        select(MaterialLot).where(MaterialLot.material_id == material.id)
                    )
                ).all()
            )
            movements = list(
                (
                    await session.scalars(
                        select(StockMovement).where(
                            StockMovement.material_id == material.id,
                            StockMovement.movement_type != "CORRECTION",
                        )
                    )
                ).all()
            )
            active = list(
                (
                    await session.scalars(
                        select(Reservation).where(
                            Reservation.material_id == material.id,
                            Reservation.status == "ACTIVE",
                        )
                    )
                ).all()
            )
            quantity = D(rng.randint(1, 60))
            choice = rng.choice(
                ["receipt", "receipt", "accept", "issue", "correction", "reserve", "release"]
            )
            try:
                if choice == "receipt" or not lots:
                    if not lot_codes or rng.random() < 0.5:
                        lot_codes.append(f"LOT-R{step}")
                    await inventory_service.record_receipt(
                        session,
                        principal,
                        ktn.id,
                        material_id=material.id,
                        lot_code=rng.choice(lot_codes),
                        quantity=quantity,
                        accept=rng.random() < 0.6,
                    )
                elif choice == "accept":
                    await inventory_service.accept_lot(session, principal, rng.choice(lots).id)
                elif choice == "issue":
                    await inventory_service.record_issue(
                        session,
                        principal,
                        ktn.id,
                        material_id=material.id,
                        lot_id=rng.choice(lots).id,
                        quantity=quantity,
                        order_id=rng.choice([None, *orders]),
                        reason="random issue",
                    )
                elif choice == "correction" and movements:
                    await inventory_service.record_correction(
                        session,
                        principal,
                        movement_id=rng.choice(movements).id,
                        quantity_delta=quantity * rng.choice([-1, 1]),
                        reason="random correction",
                    )
                elif choice == "reserve":
                    await inventory_service.reserve_material(
                        session,
                        organization_id=identity.organization.id,
                        factory_id=ktn.id,
                        material_id=material.id,
                        order_id=rng.choice(orders),
                        quantity=quantity,
                        actor_user_id=principal.user_id,
                    )
                elif choice == "release" and active:
                    await inventory_service.release_reservation(
                        session, principal, rng.choice(active).id
                    )
                else:
                    await session.rollback()
                    continue
            except AppError as exc:
                assert exc.status_code == 409, exc.message
                conflicts += 1
                await session.rollback()
            else:
                successes += 1
                await session.commit()

        async with session_factory() as check:
            balance = await check.scalar(
                select(MaterialBalance).where(MaterialBalance.material_id == material.id)
            )
            ledger_total = await check.scalar(
                select(sa.func.coalesce(sa.func.sum(StockMovement.quantity), 0))
                .join(MaterialLot, MaterialLot.id == StockMovement.lot_id)
                .where(
                    StockMovement.material_id == material.id,
                    MaterialLot.status == "ACCEPTED",
                )
            )
            reserved_total = await check.scalar(
                select(sa.func.coalesce(sa.func.sum(Reservation.quantity), 0)).where(
                    Reservation.material_id == material.id, Reservation.status == "ACTIVE"
                )
            )
        if balance is None:
            continue
        assert balance.on_hand_accepted == ledger_total, step
        assert balance.reserved == reserved_total, step
        assert D(0) <= balance.reserved <= balance.on_hand_accepted

    assert successes >= 15
    assert conflicts >= 1


async def test_overdue_order_short_on_material_is_shortage(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    overdue = utcnow().date() - timedelta(days=10)
    material, order = await make_material_order(
        db_session, identity.organization, ktn, quantity=100, due_date=overdue
    )
    # A receipt expected after the (past) due date must not rescue the order.
    db_session.add(
        ExpectedReceipt(
            organization_id=identity.organization.id,
            factory_id=ktn.id,
            material_id=material.id,
            quantity=D(500),
            expected_date=utcnow().date() + timedelta(days=5),
            supplier_ref="SUP-LATE",
            status="OPEN",
        )
    )
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    await _receive(storekeeper, ktn.id, material.id, "40", "LOT-OD1")
    async with session_factory() as check:
        refreshed = await check.get(Order, order.id)
    assert refreshed is not None and refreshed.material_state == "SHORTAGE"


async def test_issue_from_quarantined_lot_conflicts(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    material = await make_material(db_session, organization=identity.organization)
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    await _receive(storekeeper, ktn.id, material.id, "50", "LOT-OK1")
    quarantined = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/receipts",
        json={
            "material_id": str(material.id),
            "lot_code": "LOT-QI1",
            "quantity": "20",
            "accept": False,
        },
        headers=_key(),
    )
    assert quarantined.status_code == 201
    issue = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/stock/issues",
        json={
            "material_id": str(material.id),
            "lot_id": quarantined.json()["lot"]["id"],
            "quantity": "5",
        },
        headers=_key(),
    )
    assert issue.status_code == 409
    assert issue.json()["error"]["code"] == "CONFLICT"
    balance = await _balance(session_factory, ktn.id, material.id)
    assert balance.on_hand_accepted == D(50)


async def test_reservation_requires_material_on_order_bom(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    _, order = await make_material_order(db_session, identity.organization, ktn)
    other = await make_material(db_session, organization=identity.organization)
    await db_session.commit()
    storekeeper = await login_as(client, session_factory, "storekeeper@demo.test")
    await _receive(storekeeper, ktn.id, other.id, "50", "LOT-NB1")
    response = await storekeeper.post(
        f"/api/v1/factories/{ktn.id}/reservations",
        json={"material_id": str(other.id), "order_id": str(order.id), "quantity": "5"},
        headers=_key(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["field_errors"][0]["field"] == "material_id"
