"""Quality tables: policy versions, inspections, defects, holds, releases.

See ``docs/architecture/backend-contracts.md`` section 2 ("Quality").

``quality_holds.release_id -> quality_releases`` is declared ``use_alter=True``
so the hand-written migration can create ``quality_holds`` before
``quality_releases`` (matching the contract's listed table order) and add
this one foreign key afterwards via a deferred ``ALTER TABLE``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    composite_factory_fk,
    created_at,
    enum_check,
    factory_id_col,
    org_factory_index,
    org_fk,
    uuid_pk,
)
from app.domain.vocab import (
    DefectSeverity,
    InspectionResult,
    InspectionType,
    PolicyStatus,
    QualityHoldStatus,
)


class QualityPolicyVersion(Base):
    __tablename__ = "quality_policy_versions"
    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "code",
            "version_no",
            name="uq_quality_policy_versions_organization_id_code_version_no",
        ),
        enum_check("status_valid", "status", [s.value for s in PolicyStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_demo: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rules: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at()


class Inspection(Base):
    __tablename__ = "inspections"
    __table_args__ = (
        composite_factory_fk("inspections"),
        org_factory_index("inspections"),
        enum_check("inspection_type_valid", "inspection_type", [t.value for t in InspectionType]),
        enum_check("result_valid", "result", [r.value for r in InspectionResult]),
        sa.CheckConstraint("inspected_units >= 0", name="inspected_units_non_negative"),
        sa.CheckConstraint("defective_units >= 0", name="defective_units_non_negative"),
        sa.CheckConstraint(
            "defective_units <= inspected_units", name="defective_units_within_inspected"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    line_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("lines.id"), nullable=True)
    inspection_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    inspected_units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    defective_units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("quality_policy_versions.id"), nullable=False
    )
    result: Mapped[str] = mapped_column(sa.Text, nullable=False)
    inspected_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    inspected_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


class DefectObservation(Base):
    __tablename__ = "defect_observations"
    __table_args__ = (
        enum_check("severity_valid", "severity", [s.value for s in DefectSeverity]),
        sa.CheckConstraint("count > 0", name="count_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False
    )
    defect_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    severity: Mapped[str] = mapped_column(sa.Text, nullable=False)
    count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("style_operations.id"), nullable=True
    )


class QualityHold(Base):
    __tablename__ = "quality_holds"
    __table_args__ = (
        composite_factory_fk("quality_holds"),
        org_factory_index("quality_holds"),
        enum_check("status_valid", "status", [s.value for s in QualityHoldStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    inspection_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("inspections.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()
    released_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("quality_releases.id", use_alter=True), nullable=True
    )


class QualityRelease(Base):
    __tablename__ = "quality_releases"
    __table_args__ = (
        composite_factory_fk("quality_releases"),
        org_factory_index("quality_releases"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("inspections.id"), nullable=False
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("quality_policy_versions.id"), nullable=False
    )
    released_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    released_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
