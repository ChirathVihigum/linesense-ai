"""Decision tables: recommendations, approvals, the append-only audit log.

See ``docs/architecture/backend-contracts.md`` section 2 ("Decisions and operations").

``audit_events`` deliberately carries **no** foreign keys at all (not even to
``organizations``/``factories``): it is an append-only log that must survive
deletion of the rows it references, so ``organization_id``/``factory_id``/
``run_id`` are plain columns.
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
    ActorType,
    ApprovalDecision,
    AuditOutcome,
    GeneratedBy,
    RecommendationKind,
    RecommendationStatus,
)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        composite_factory_fk("recommendations"),
        org_factory_index("recommendations"),
        sa.Index("ix_recommendations_factory_id_status", "factory_id", "status"),
        enum_check("kind_valid", "kind", [k.value for k in RecommendationKind]),
        enum_check("status_valid", "status", [s.value for s in RecommendationStatus]),
        enum_check("generated_by_valid", "generated_by", [g.value for g in GeneratedBy]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("analysis_runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    proposal: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    proposal_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    input_versions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_refs: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    generated_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    proposed_by_agent: Mapped[str] = mapped_column(sa.Text, nullable=False)
    proposer_user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    applied_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    superseded_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    created_at: Mapped[datetime] = created_at()


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        enum_check("decision_valid", "decision", [d.value for d in ApprovalDecision]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("recommendations.id"), nullable=False, unique=True
    )
    decision: Mapped[str] = mapped_column(sa.Text, nullable=False)
    decided_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    proposal_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)


class AuditEvent(Base):
    """Append-only: ``linesense_app`` gets only ``SELECT, INSERT`` on this table."""

    __tablename__ = "audit_events"
    __table_args__ = (
        org_factory_index("audit_events"),
        sa.Index("ix_audit_events_organization_id_created_at", "organization_id", "created_at"),
        enum_check("actor_type_valid", "actor_type", [t.value for t in ActorType]),
        enum_check("outcome_valid", "outcome", [o.value for o in AuditOutcome]),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)
    actor_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    outcome: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at()
