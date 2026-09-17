"""Inventory tables: lots, stock movements, balances, reservations, receipts.

See ``docs/architecture/backend-contracts.md`` section 2 ("Inventory").
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    composite_factory_fk,
    created_at,
    enum_check,
    factory_id_col,
    money,
    org_factory_index,
    org_fk,
    updated_at,
    uuid_pk,
)
from app.domain.vocab import (
    ExpectedReceiptStatus,
    MaterialLotStatus,
    MovementType,
    ReservationStatus,
)


class MaterialLot(Base):
    __tablename__ = "material_lots"
    __table_args__ = (
        composite_factory_fk("material_lots"),
        org_factory_index("material_lots"),
        sa.UniqueConstraint("factory_id", "lot_code", name="uq_material_lots_factory_id_lot_code"),
        enum_check("status_valid", "status", [s.value for s in MaterialLotStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    material_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("materials.id"), nullable=False)
    lot_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        composite_factory_fk("stock_movements"),
        org_factory_index("stock_movements"),
        sa.Index(
            "ix_stock_movements_factory_id_material_id_created_at",
            "factory_id",
            "material_id",
            "created_at",
        ),
        enum_check("movement_type_valid", "movement_type", [m.value for m in MovementType]),
        sa.CheckConstraint(
            "(movement_type = 'RECEIPT' AND quantity > 0) OR "
            "(movement_type = 'ISSUE' AND quantity < 0) OR "
            "(movement_type = 'CORRECTION' AND quantity <> 0)",
            name="quantity_sign_matches_movement_type",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    material_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("materials.id"), nullable=False)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("material_lots.id"), nullable=True
    )
    movement_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    corrects_movement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("stock_movements.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("orders.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()


class MaterialBalance(Base):
    __tablename__ = "material_balances"
    __table_args__ = (
        composite_factory_fk("material_balances"),
        org_factory_index("material_balances"),
        sa.UniqueConstraint(
            "factory_id", "material_id", name="uq_material_balances_factory_id_material_id"
        ),
        sa.CheckConstraint("on_hand_accepted >= 0", name="on_hand_accepted_non_negative"),
        sa.CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        sa.CheckConstraint("reserved <= on_hand_accepted", name="reserved_within_on_hand"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    material_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("materials.id"), nullable=False)
    on_hand_accepted: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    reserved: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    updated_at: Mapped[datetime] = updated_at()


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        composite_factory_fk("reservations"),
        org_factory_index("reservations"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        enum_check("status_valid", "status", [s.value for s in ReservationStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    material_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("materials.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("recommendations.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()


class ExpectedReceipt(Base):
    __tablename__ = "expected_receipts"
    __table_args__ = (
        composite_factory_fk("expected_receipts"),
        org_factory_index("expected_receipts"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        enum_check("status_valid", "status", [s.value for s in ExpectedReceiptStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    material_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("materials.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    expected_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    supplier_ref: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = created_at()
