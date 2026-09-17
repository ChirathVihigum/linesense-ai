"""Async builders for the ORM models, reused by every task's test suite.

Every builder takes the active ``AsyncSession`` plus keyword overrides for
any column, creates sensible parents that are not supplied (org, factory,
customer, style, BOM, ...), adds the row(s) to the session, and flushes
(never commits: the caller's transaction/truncation fixture owns that).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BomVersion,
    Customer,
    Factory,
    Line,
    LineCapacitySlot,
    Material,
    MaterialBalance,
    Membership,
    Order,
    Organization,
    Style,
    StyleOperation,
    User,
)
from app.domain.vocab import MaterialState, OrderSource, ProductionState, QualityState


def _slug() -> str:
    return uuid.uuid4().hex[:10]


async def make_org(session: AsyncSession, **overrides: Any) -> Organization:
    suffix = _slug()
    defaults: dict[str, Any] = {
        "name": f"Test Org {suffix}",
        "slug": f"test-org-{suffix}",
    }
    defaults.update(overrides)
    org = Organization(**defaults)
    session.add(org)
    await session.flush()
    return org


async def make_factory(
    session: AsyncSession, organization: Organization | None = None, **overrides: Any
) -> Factory:
    organization = organization or await make_org(session)
    suffix = _slug()
    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "code": f"FAC-{suffix}",
        "name": f"Test Factory {suffix}",
    }
    defaults.update(overrides)
    factory = Factory(**defaults)
    session.add(factory)
    await session.flush()
    return factory


async def make_user(session: AsyncSession, **overrides: Any) -> User:
    suffix = _slug()
    defaults: dict[str, Any] = {
        "issuer": "dev",
        "subject": f"dev|{suffix}",
        "email": f"{suffix}@example.test",
        "display_name": f"Test User {suffix}",
    }
    defaults.update(overrides)
    user = User(**defaults)
    session.add(user)
    await session.flush()
    return user


async def make_membership(
    session: AsyncSession,
    organization: Organization | None = None,
    user: User | None = None,
    **overrides: Any,
) -> Membership:
    organization = organization or await make_org(session)
    user = user or await make_user(session)
    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "user_id": user.id,
    }
    defaults.update(overrides)
    membership = Membership(**defaults)
    session.add(membership)
    await session.flush()
    return membership


async def make_style_with_operations(
    session: AsyncSession,
    organization: Organization | None = None,
    *,
    operation_count: int = 2,
    **overrides: Any,
) -> Style:
    organization = organization or await make_org(session)
    suffix = _slug()
    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "code": f"STY-{suffix}",
        "name": f"Test Style {suffix}",
        "product_type": "knit-top",
    }
    defaults.update(overrides)
    style = Style(**defaults)
    session.add(style)
    await session.flush()

    for i in range(operation_count):
        session.add(
            StyleOperation(
                style_id=style.id,
                sequence=i + 1,
                code=f"OP-{i + 1}",
                name=f"Operation {i + 1}",
                sam_minutes=1.5,
                skill_code="SEW",
                machine_type="single-needle",
            )
        )
    await session.flush()
    return style


async def make_material(
    session: AsyncSession, organization: Organization | None = None, **overrides: Any
) -> Material:
    organization = organization or await make_org(session)
    suffix = _slug()
    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "code": f"MAT-{suffix}",
        "name": f"Test Material {suffix}",
        "unit": "m",
        "safety_stock": 0,
        "lead_time_days": 7,
    }
    defaults.update(overrides)
    material = Material(**defaults)
    session.add(material)
    await session.flush()
    return material


async def _make_bom_version(session: AsyncSession, style: Style, **overrides: Any) -> BomVersion:
    defaults: dict[str, Any] = {
        "style_id": style.id,
        "version_no": 1,
        "is_active": True,
    }
    defaults.update(overrides)
    bom_version = BomVersion(**defaults)
    session.add(bom_version)
    await session.flush()
    return bom_version


async def make_order(
    session: AsyncSession,
    organization: Organization | None = None,
    factory: Factory | None = None,
    customer: Customer | None = None,
    style: Style | None = None,
    bom_version: BomVersion | None = None,
    **overrides: Any,
) -> Order:
    organization = organization or await make_org(session)
    factory = factory or await make_factory(session, organization=organization)
    style = style or await make_style_with_operations(session, organization=organization)
    bom_version = bom_version or await _make_bom_version(session, style)
    suffix = _slug()
    if customer is None:
        customer = Customer(
            organization_id=organization.id, code=f"CUST-{suffix}", name=f"Customer {suffix}"
        )
        session.add(customer)
        await session.flush()

    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "factory_id": factory.id,
        "customer_id": customer.id,
        "style_id": style.id,
        "bom_version_id": bom_version.id,
        "external_ref": f"PO-{suffix}",
        "quantity": 100,
        "due_date": date(2026, 12, 31),
        "production_state": ProductionState.DRAFT.value,
        "material_state": MaterialState.UNKNOWN.value,
        "quality_state": QualityState.NOT_INSPECTED.value,
        "source": OrderSource.MANUAL.value,
    }
    defaults.update(overrides)
    order = Order(**defaults)
    session.add(order)
    await session.flush()
    return order


async def make_line(
    session: AsyncSession,
    organization: Organization | None = None,
    factory: Factory | None = None,
    **overrides: Any,
) -> Line:
    organization = organization or await make_org(session)
    factory = factory or await make_factory(session, organization=organization)
    suffix = _slug()
    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "factory_id": factory.id,
        "code": f"LINE-{suffix}",
        "name": f"Line {suffix}",
        "operator_count": 20,
        "is_active": True,
    }
    defaults.update(overrides)
    line = Line(**defaults)
    session.add(line)
    await session.flush()
    return line


async def make_slot(
    session: AsyncSession, line: Line | None = None, **overrides: Any
) -> LineCapacitySlot:
    line = line or await make_line(session)
    defaults: dict[str, Any] = {
        "organization_id": line.organization_id,
        "factory_id": line.factory_id,
        "line_id": line.id,
        "slot_date": date(2026, 12, 1),
        "shift_code": "A",
        "available_operator_minutes": 4800,
        "planned_efficiency": 0.75,
    }
    defaults.update(overrides)
    slot = LineCapacitySlot(**defaults)
    session.add(slot)
    await session.flush()
    return slot


async def make_balance(
    session: AsyncSession,
    organization: Organization | None = None,
    factory: Factory | None = None,
    material: Material | None = None,
    **overrides: Any,
) -> MaterialBalance:
    organization = organization or await make_org(session)
    factory = factory or await make_factory(session, organization=organization)
    material = material or await make_material(session, organization=organization)
    defaults: dict[str, Any] = {
        "organization_id": organization.id,
        "factory_id": factory.id,
        "material_id": material.id,
        "on_hand_accepted": 0,
        "reserved": 0,
    }
    defaults.update(overrides)
    balance = MaterialBalance(**defaults)
    session.add(balance)
    await session.flush()
    return balance


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
