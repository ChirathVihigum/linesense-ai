"""Identity tables: organizations, factories, users, memberships, roles, sessions.

See ``docs/architecture/backend-contracts.md`` section 2 ("Identity").
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import created_at, enum_check, org_fk, uuid_pk
from app.domain.vocab import ROLES


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at()


class Factory(Base):
    __tablename__ = "factories"
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "code", name="uq_factories_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_factories_organization_id_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    timezone: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="Asia/Colombo")
    created_at: Mapped[datetime] = created_at()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    issuer: Mapped[str] = mapped_column(sa.Text, nullable=False)
    subject: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = created_at()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_memberships_organization_id_user_id"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = created_at()


class RoleAssignment(Base):
    """A role for a membership, scoped to one factory or, if null, the org.

    ``organization_id`` deliberately does not live on this table: the org is
    reachable via ``membership_id -> memberships.organization_id``. Because
    of that, ``factory_id`` cannot use the composite tenant-safety foreign
    key (it needs an ``organization_id`` column on *this* table); it is a
    plain FK to ``factories.id`` instead.
    """

    __tablename__ = "role_assignments"
    __table_args__ = (
        sa.UniqueConstraint(
            "membership_id",
            "factory_id",
            "role",
            name="uq_role_assignments_membership_id_factory_id_role",
        ),
        enum_check("role_valid", "role", ROLES),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    membership_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    factory_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("factories.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
