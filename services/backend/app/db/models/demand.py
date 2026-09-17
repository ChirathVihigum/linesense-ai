"""Demand tables: customers, styles, BOMs, orders.

See ``docs/architecture/backend-contracts.md`` section 2 ("Demand").
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
from app.domain.vocab import MaterialState, MaterialUnit, OrderSource, ProductionState, QualityState


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "code", name="uq_customers_organization_id_code"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


class Style(Base):
    __tablename__ = "styles"
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "code", name="uq_styles_organization_id_code"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    product_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


class StyleOperation(Base):
    __tablename__ = "style_operations"
    __table_args__ = (
        sa.UniqueConstraint("style_id", "sequence", name="uq_style_operations_style_id_sequence"),
        sa.CheckConstraint("sam_minutes > 0", name="sam_minutes_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    style_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("styles.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sam_minutes: Mapped[Decimal] = mapped_column(money(10, 4), nullable=False)
    skill_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    machine_type: Mapped[str] = mapped_column(sa.Text, nullable=False)


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "code", name="uq_materials_organization_id_code"),
        enum_check("unit_valid", "unit", [unit.value for unit in MaterialUnit]),
        sa.CheckConstraint("safety_stock >= 0", name="safety_stock_non_negative"),
        sa.CheckConstraint("lead_time_days >= 0", name="lead_time_days_non_negative"),
        sa.CheckConstraint("pack_size IS NULL OR pack_size > 0", name="pack_size_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    unit: Mapped[str] = mapped_column(sa.Text, nullable=False)
    safety_stock: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    lead_time_days: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    pack_size: Mapped[Decimal | None] = mapped_column(money(14, 4), nullable=True)
    created_at: Mapped[datetime] = created_at()


class BomVersion(Base):
    __tablename__ = "bom_versions"
    __table_args__ = (
        sa.UniqueConstraint("style_id", "version_no", name="uq_bom_versions_style_id_version_no"),
        sa.Index(
            "uq_bom_versions_one_active_per_style",
            "style_id",
            unique=True,
            postgresql_where=sa.text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    style_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("styles.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at()


class BomLine(Base):
    __tablename__ = "bom_lines"
    __table_args__ = (
        sa.CheckConstraint("quantity_per_unit > 0", name="quantity_per_unit_positive"),
        sa.CheckConstraint(
            "wastage_fraction >= 0 AND wastage_fraction < 1", name="wastage_fraction_range"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bom_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("bom_versions.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("materials.id"), nullable=False)
    quantity_per_unit: Mapped[Decimal] = mapped_column(money(14, 6), nullable=False)
    unit: Mapped[str] = mapped_column(sa.Text, nullable=False)
    wastage_fraction: Mapped[Decimal] = mapped_column(money(6, 4), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        composite_factory_fk("orders"),
        org_factory_index("orders"),
        sa.Index("ix_orders_factory_id_due_date", "factory_id", "due_date"),
        sa.UniqueConstraint(
            "organization_id", "external_ref", name="uq_orders_organization_id_external_ref"
        ),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint("produced_units >= 0", name="produced_units_non_negative"),
        sa.CheckConstraint("packed_units >= 0", name="packed_units_non_negative"),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        enum_check(
            "production_state_valid", "production_state", [s.value for s in ProductionState]
        ),
        enum_check("material_state_valid", "material_state", [s.value for s in MaterialState]),
        enum_check("quality_state_valid", "quality_state", [s.value for s in QualityState]),
        enum_check("source_valid", "source", [s.value for s in OrderSource]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    customer_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("customers.id"), nullable=False)
    style_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("styles.id"), nullable=False)
    bom_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("bom_versions.id"), nullable=False
    )
    external_ref: Mapped[str] = mapped_column(sa.Text, nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    produced_units: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    packed_units: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    due_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    priority: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("3"))
    production_state: Mapped[str] = mapped_column(sa.Text, nullable=False)
    material_state: Mapped[str] = mapped_column(sa.Text, nullable=False)
    quality_state: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
