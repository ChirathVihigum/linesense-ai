"""Capacity tables: lines, capabilities, capacity slots, allocations.

See ``docs/architecture/backend-contracts.md`` section 2 ("Capacity").
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
    uuid_pk,
)
from app.domain.vocab import AllocationStatus, ShiftCode


class Line(Base):
    __tablename__ = "lines"
    __table_args__ = (
        composite_factory_fk("lines"),
        org_factory_index("lines"),
        sa.UniqueConstraint("factory_id", "code", name="uq_lines_factory_id_code"),
        sa.CheckConstraint("operator_count > 0", name="operator_count_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    operator_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    created_at: Mapped[datetime] = created_at()


class LineCapability(Base):
    __tablename__ = "line_capabilities"
    __table_args__ = (
        sa.UniqueConstraint(
            "line_id", "skill_code", name="uq_line_capabilities_line_id_skill_code"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    line_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("lines.id", ondelete="CASCADE"), nullable=False
    )
    skill_code: Mapped[str] = mapped_column(sa.Text, nullable=False)


class LineCapacitySlot(Base):
    __tablename__ = "line_capacity_slots"
    __table_args__ = (
        composite_factory_fk("line_capacity_slots"),
        org_factory_index("line_capacity_slots"),
        sa.UniqueConstraint(
            "line_id",
            "slot_date",
            "shift_code",
            name="uq_line_capacity_slots_line_id_slot_date_shift_code",
        ),
        enum_check("shift_code_valid", "shift_code", [s.value for s in ShiftCode]),
        sa.CheckConstraint(
            "available_operator_minutes >= 0", name="available_operator_minutes_non_negative"
        ),
        sa.CheckConstraint(
            "planned_efficiency > 0 AND planned_efficiency <= 1", name="planned_efficiency_range"
        ),
        sa.CheckConstraint(
            "allocated_standard_minutes >= 0", name="allocated_standard_minutes_non_negative"
        ),
        sa.CheckConstraint(
            "allocated_standard_minutes <= available_operator_minutes * planned_efficiency",
            name="allocated_within_capacity",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    line_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("lines.id"), nullable=False)
    slot_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    shift_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    available_operator_minutes: Mapped[Decimal] = mapped_column(money(12, 2), nullable=False)
    planned_efficiency: Mapped[Decimal] = mapped_column(money(5, 4), nullable=False)
    allocated_standard_minutes: Mapped[Decimal] = mapped_column(
        money(12, 2), nullable=False, server_default=sa.text("0")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (
        composite_factory_fk("allocations"),
        org_factory_index("allocations"),
        sa.CheckConstraint("standard_minutes > 0", name="standard_minutes_positive"),
        sa.CheckConstraint("units > 0", name="units_positive"),
        enum_check("status_valid", "status", [s.value for s in AllocationStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    slot_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("line_capacity_slots.id"), nullable=False
    )
    standard_minutes: Mapped[Decimal] = mapped_column(money(12, 2), nullable=False)
    units: Mapped[Decimal] = mapped_column(money(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("recommendations.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()
