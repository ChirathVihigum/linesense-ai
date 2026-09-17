"""Authentication test helpers.

``DEMO_IDENTITIES`` mirrors backend-contracts.md section 9 and the dev IdP's
``devtools/dev_oidc/users.json`` (a unit test keeps the two in sync). Task 6
owns the canonical list in ``app/seed/generator.py`` (re-exported from
``app/seed/identities.py``); this module just imports it so the seed
generator and the test helpers never drift apart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.oidc import normalize_issuer
from app.auth.sessions import SESSION_COOKIE, create_session
from app.db.models import Factory, Membership, Organization, RoleAssignment, User
from app.seed.generator import (
    DEMO_FACTORIES,
    DEMO_IDENTITIES,
    DEMO_ORG_NAME,
    DEMO_ORG_SLUG,
    DemoIdentity,
)
from app.settings import Settings

DEFAULT_DEV_ISSUER = "http://127.0.0.1:8090"

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def demo_identity(email: str) -> DemoIdentity | None:
    return next((identity for identity in DEMO_IDENTITIES if identity[0] == email), None)


@dataclass
class IdentityFixture:
    organization: Organization
    factories: dict[str, Factory]
    users: dict[str, User]
    memberships: dict[str, Membership]


async def _ensure_org(session: AsyncSession) -> tuple[Organization, dict[str, Factory]]:
    org = await session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    if org is None:
        org = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG)
        session.add(org)
        await session.flush()
    factories: dict[str, Factory] = {}
    for code, name in DEMO_FACTORIES:
        factory = await session.scalar(
            select(Factory).where(Factory.organization_id == org.id, Factory.code == code)
        )
        if factory is None:
            factory = Factory(organization_id=org.id, code=code, name=name)
            session.add(factory)
            await session.flush()
        factories[code] = factory
    return org, factories


async def _ensure_user(
    session: AsyncSession, *, issuer: str, subject: str, email: str, display_name: str
) -> User:
    user = await session.scalar(select(User).where(User.issuer == issuer, User.subject == subject))
    if user is None:
        user = User(issuer=issuer, subject=subject, email=email, display_name=display_name)
        session.add(user)
        await session.flush()
    return user


async def _ensure_membership(
    session: AsyncSession,
    *,
    organization: Organization,
    factories: dict[str, Factory],
    user: User,
    role: str,
    factory_code: str | None,
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(
            Membership.organization_id == organization.id, Membership.user_id == user.id
        )
    )
    if membership is None:
        membership = Membership(organization_id=organization.id, user_id=user.id)
        session.add(membership)
        await session.flush()
    factory_id = factories[factory_code].id if factory_code is not None else None
    existing = await session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.membership_id == membership.id,
            RoleAssignment.role == role,
            RoleAssignment.factory_id.is_(None)
            if factory_id is None
            else RoleAssignment.factory_id == factory_id,
        )
    )
    if existing is None:
        session.add(RoleAssignment(membership_id=membership.id, factory_id=factory_id, role=role))
        await session.flush()
    return membership


async def seed_identity(
    session: AsyncSession, *, issuer: str = DEFAULT_DEV_ISSUER
) -> IdentityFixture:
    """Create (idempotently) the contracts section 9 org, factories, users and roles."""
    issuer = normalize_issuer(issuer)
    organization, factories = await _ensure_org(session)
    users: dict[str, User] = {}
    memberships: dict[str, Membership] = {}
    for email, subject, display_name, role, factory_code in DEMO_IDENTITIES:
        user = await _ensure_user(
            session, issuer=issuer, subject=subject, email=email, display_name=display_name
        )
        users[email] = user
        memberships[email] = await _ensure_membership(
            session,
            organization=organization,
            factories=factories,
            user=user,
            role=role,
            factory_code=factory_code,
        )
    return IdentityFixture(
        organization=organization, factories=factories, users=users, memberships=memberships
    )


def app_settings(client: AsyncClient) -> Settings:
    """The ``Settings`` of the ASGI app behind an ``httpx`` test client."""
    transport = client._transport  # noqa: SLF001 - test-only introspection
    if not isinstance(transport, ASGITransport):
        raise TypeError("login_as requires an httpx client using ASGITransport")
    settings = transport.app.state.settings  # type: ignore[attr-defined]
    assert isinstance(settings, Settings)
    return settings


@dataclass
class AuthedClient:
    """Wraps a test client; unsafe methods automatically carry CSRF headers."""

    client: AsyncClient
    user: User
    csrf_token: str
    origin: str
    session_id: uuid.UUID
    me: dict[str, Any] | None = None

    def _headers(self, method: str, headers: dict[str, str] | None) -> dict[str, str]:
        merged = dict(headers or {})
        if method.upper() in _UNSAFE_METHODS:
            merged.setdefault("X-CSRF-Token", self.csrf_token)
            merged.setdefault("Origin", self.origin)
        return merged

    async def request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> Response:
        return await self.client.request(
            method, url, headers=self._headers(method, headers), **kwargs
        )

    async def get(self, url: str, **kwargs: Any) -> Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> Response:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> Response:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Response:
        return await self.request("DELETE", url, **kwargs)


async def login_as(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
) -> AuthedClient:
    """Authenticate ``client`` as ``email`` without an OIDC round trip.

    Demo identities (section 9) get their org/factory/membership/role rows
    created if absent; any other email gets a bare user with no membership.
    Each ``AsyncClient`` holds one identity at a time (the cookie jar is
    shared), so use separate clients for multi-user scenarios.
    """
    settings = app_settings(client)
    identity = demo_identity(email)
    async with session_factory() as session:
        if identity is not None:
            _, subject, display_name, role, factory_code = identity
            organization, factories = await _ensure_org(session)
            user = await _ensure_user(
                session,
                issuer=normalize_issuer(settings.oidc_issuer),
                subject=subject,
                email=email,
                display_name=display_name,
            )
            await _ensure_membership(
                session,
                organization=organization,
                factories=factories,
                user=user,
                role=role,
                factory_code=factory_code,
            )
        else:
            local_part = email.split("@", 1)[0]
            user = await _ensure_user(
                session,
                issuer=normalize_issuer(settings.oidc_issuer),
                subject=f"dev|{local_part}",
                email=email,
                display_name=local_part,
            )
        raw_token, record = await create_session(
            session, user.id, max_age_seconds=settings.session_max_age_seconds
        )
        await session.commit()

    client.cookies.set(SESSION_COOKIE, raw_token)
    authed = AuthedClient(
        client=client,
        user=user,
        csrf_token=record.csrf_token,
        origin=settings.public_origin,
        session_id=record.id,
    )
    response = await client.get("/api/v1/me")
    if response.status_code == 200:
        authed.me = response.json()
        assert authed.me is not None
        assert authed.me["csrf_token"] == record.csrf_token
    elif response.status_code != 403:
        raise AssertionError(f"/api/v1/me failed: {response.status_code} {response.text}")
    return authed


__all__ = [
    "DEMO_IDENTITIES",
    "AuthedClient",
    "IdentityFixture",
    "login_as",
    "seed_identity",
]
