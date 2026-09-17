"""Workflow tables: analysis runs, snapshots, agent tasks/results, jobs.

See ``docs/architecture/backend-contracts.md`` section 2 ("Workflow").

``analysis_runs.snapshot_id -> run_snapshots`` and ``run_snapshots.run_id ->
analysis_runs`` form a genuine two-table cycle, so the former is declared
``use_alter=True``: the hand-written migration creates both tables and adds
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
    org_fk,
    uuid_pk,
)
from app.domain.vocab import AgentRecipient, JobStatus, RunStatus, TaskStatus


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        composite_factory_fk("analysis_runs"),
        enum_check("status_valid", "status", [s.value for s in RunStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("run_snapshots.id", use_alter=True), nullable=True
    )
    replan_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    model_calls_used: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    model_calls_limit: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("12")
    )
    tokens_used: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    token_budget: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("200000")
    )
    llm_provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    llm_model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    degraded_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    created_at: Mapped[datetime] = created_at()


class RunSnapshot(Base):
    """Immutable: no update path is ever exposed."""

    __tablename__ = "run_snapshots"
    __table_args__ = (composite_factory_fk("run_snapshots"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("analysis_runs.id"), nullable=False)
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    order_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("orders.id"), nullable=False)
    input_versions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        composite_factory_fk("agent_tasks"),
        sa.Index("ix_agent_tasks_run_id", "run_id"),
        enum_check("recipient_valid", "recipient", [r.value for r in AgentRecipient]),
        enum_check("status_valid", "status", [s.value for s in TaskStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("analysis_runs.id"), nullable=False)
    organization_id: Mapped[uuid.UUID] = org_fk()
    factory_id: Mapped[uuid.UUID] = factory_id_col()
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("agent_tasks.id"), nullable=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False, unique=True)
    sender: Mapped[str] = mapped_column(sa.Text, nullable=False)
    recipient: Mapped[str] = mapped_column(sa.Text, nullable=False)
    task_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    round: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    envelope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("3")
    )
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )


class AgentResultRecord(Base):
    __tablename__ = "agent_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("agent_tasks.id"), nullable=False, unique=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("analysis_runs.id"), nullable=False)
    schema_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (sa.Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("analysis_runs.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        sa.Index("ix_jobs_queue_status_available_at", "queue", "status", "available_at"),
        enum_check("status_valid", "status", [s.value for s in JobStatus]),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    queue: Mapped[str] = mapped_column(sa.Text, nullable=False)
    job_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(sa.Text, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("3")
    )
    available_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    leased_until: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
