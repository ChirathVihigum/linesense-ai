"""Industrial engineering tables: operator aliases, skills, staffing, cycle data.

See ``docs/architecture/backend-contracts.md`` section 2 ("Industrial engineering").
Pseudonymous only: no names or personal attributes anywhere on these tables.
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
    factory_id_col,
    money,
    org_factory_index,
    org_fk,
    uuid_pk,
)


class OperatorAlias(Base):
    __tablename__ = "operator_aliases"
    __table_args__ = (
        composite_factory_fk("operator_aliases"),
        org_factory_index("operator_aliases"),
        sa.UniqueConstraint(
            "factory_id", "alias_code", name="uq_operator_aliases_factory_id_alias_code"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    alias_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    line_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("lines.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    created_at: Mapped[datetime] = created_at()


class SkillRecord(Base):
    __tablename__ = "skill_records"
    __table_args__ = (
        sa.UniqueConstraint(
            "operator_alias_id", "skill_code", name="uq_skill_records_operator_alias_id_skill_code"
        ),
        sa.CheckConstraint("level BETWEEN 1 AND 4", name="level_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    operator_alias_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("operator_aliases.id", ondelete="CASCADE"), nullable=False
    )
    skill_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    level: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class OperationStaffing(Base):
    __tablename__ = "operation_staffing"
    __table_args__ = (
        composite_factory_fk("operation_staffing"),
        org_factory_index("operation_staffing"),
        sa.UniqueConstraint(
            "line_id",
            "style_id",
            "operation_id",
            name="uq_operation_staffing_line_id_style_id_operation_id",
        ),
        sa.CheckConstraint("parallel_operators > 0", name="parallel_operators_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    line_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("lines.id"), nullable=False)
    style_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("styles.id"), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("style_operations.id"), nullable=False
    )
    parallel_operators: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class CycleObservation(Base):
    __tablename__ = "cycle_observations"
    __table_args__ = (
        composite_factory_fk("cycle_observations"),
        org_factory_index("cycle_observations"),
        sa.CheckConstraint("observed_seconds > 0", name="observed_seconds_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    line_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("lines.id"), nullable=False)
    style_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("styles.id"), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("style_operations.id"), nullable=False
    )
    operator_alias_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("operator_aliases.id"), nullable=False
    )
    observed_seconds: Mapped[Decimal] = mapped_column(money(10, 2), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    is_outlier: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    outlier_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_at()


class LineMeasurement(Base):
    __tablename__ = "line_measurements"
    __table_args__ = (
        composite_factory_fk("line_measurements"),
        org_factory_index("line_measurements"),
        sa.CheckConstraint("hours > 0", name="hours_positive"),
        sa.CheckConstraint("units_output >= 0", name="units_output_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    line_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("lines.id"), nullable=False)
    style_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("styles.id"), nullable=False)
    measured_on: Mapped[date] = mapped_column(sa.Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(money(6, 2), nullable=False)
    units_output: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = created_at()
