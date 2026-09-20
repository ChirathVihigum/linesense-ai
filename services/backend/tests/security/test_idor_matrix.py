"""Route-generated IDOR matrix (task-25-brief.md req. 3).

Rather than hand-listing every scoped route (and rotting the moment a new
one is added), this walks the live ``FastAPI`` app's ``routes`` -- exactly
the routes ``app.main.create_app`` actually registers -- and, for every
``GET`` route whose path names at least one path parameter (an "item route"
such as ``/orders/{order_id}``, or a "factory-scoped route" such as
``/factories/{factory_id}/orders``), asserts:

* an unauthenticated caller gets 401 (checked with no path substitution
  beyond filler UUIDs -- ``get_principal`` rejects before any path parameter
  is even looked at), and
* a caller with a role in a *different* factory (``byg.planner@demo.test``,
  who holds no role anywhere in KTN) gets 404 for the same route with a real
  KTN factory id in ``{factory_id}`` and a real KTN order id in
  ``{order_id}``, and a random (therefore also nonexistent) id everywhere
  else.

That last simplification is deliberate, not a shortcut around the intent of
an IDOR check: `app.auth.scope.load_scoped` (backend-contracts.md section 4)
answers "no row, wrong organization, and no role in the row's factory at
all" with the *same* 404 through the *same* query (`WHERE id = :id AND
organization_id = :org_id`, then a role check on the row's factory) --
a real KTN row and a nonexistent id take the identical code path. Covering
``factory_id``/``order_id`` with real KTN rows exercises the two most common
path parameters end to end; every other id (recommendation, run, document,
...) still exercises the "no such row" branch of that same function.

Only ``GET`` is checked: a write route's body/``Idempotency-Key`` would be
validated by FastAPI before or interleaved with dependency resolution
depending on parameter order, which would make a generic body-less request
ambiguous between a 422 and the scope check under test here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import BaseRoute

from app.main import create_app
from app.settings import Settings
from tests.factories import make_order
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = [pytest.mark.integration, pytest.mark.security]

# Health, auth and internal routes use no session/no org scope (or a bearer
# service token) and are covered by their own suites; `/api/v1/me` answers
# for the caller's own identity, not a scoped resource.
ALLOWED_PREFIXES = ("/api/health", "/auth", "/internal/v1")
ALLOWED_EXACT = frozenset({"/api/v1/me", "/api/v1/imports/templates/orders.csv"})

_PARAM_RE = re.compile(r"\{(\w+)\}")


def _iter_api_routes(routes: Iterable[BaseRoute]) -> Iterable[APIRoute]:
    """Recurse through nested/lazily-included routers to every ``APIRoute``.

    Newer FastAPI wraps each ``app.include_router(...)`` as a
    ``fastapi.routing._IncludedRouter`` (holding the real ``APIRouter`` on
    ``.original_router``) rather than flattening its routes into
    ``app.routes`` directly, so a plain ``isinstance(route, APIRoute)`` scan
    of ``app.routes`` finds nothing.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _iter_api_routes(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from _iter_api_routes(route.routes)


def _scoped_get_routes() -> list[tuple[str, str]]:
    """``(path, path_template)`` for every scoped GET route the app serves."""
    app = create_app(Settings(_env_file=None, environment="test"))
    routes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for route in _iter_api_routes(app.routes):
        if "GET" not in route.methods:
            continue
        path = route.path
        if path in seen:
            continue
        if path in ALLOWED_EXACT or path.startswith(ALLOWED_PREFIXES):
            continue
        if not _PARAM_RE.search(path):
            continue  # no id to guess: not an item/factory-scoped route
        seen.add(path)
        routes.append((path, path))
    return sorted(routes)


SCOPED_GET_ROUTES = _scoped_get_routes()


def _fill(path_template: str, *, factory_id: uuid.UUID, order_id: uuid.UUID) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "factory_id":
            return str(factory_id)
        if name == "order_id":
            return str(order_id)
        return str(uuid.uuid4())

    return _PARAM_RE.sub(replace, path_template)


# A handful of routes have their own *required* query parameters beyond the
# path itself (dates, a search query, or -- for citations -- the scope's
# factory id as a query param rather than a path one). FastAPI validates
# these before the handler's own `load_scoped` scope check runs, so a request
# missing them gets 422 regardless of auth/scope; supplying a value lets the
# route reach (and this suite exercise) that scope check instead.
def _extra_query(template: str, *, factory_id: uuid.UUID) -> dict[str, str]:
    if template == "/api/v1/factories/{factory_id}/capacity":
        return {"start": "2026-01-01", "end": "2026-01-07"}
    if template == "/api/v1/factories/{factory_id}/search":
        return {"q": "sop"}
    if template == "/api/v1/citations/{chunk_id}":
        return {"factory_id": str(factory_id)}
    return {}


def test_route_inventory_is_not_empty() -> None:
    """A canary: if this ever hits zero, the walk above broke silently."""
    assert len(SCOPED_GET_ROUTES) >= 20
    assert "/api/v1/factories/{factory_id}/orders" in dict(SCOPED_GET_ROUTES)
    assert "/api/v1/orders/{order_id}" in dict(SCOPED_GET_ROUTES)


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


@pytest.fixture
async def ktn_order(db_session: AsyncSession, identity: IdentityFixture) -> uuid.UUID:
    order = await make_order(
        db_session, organization=identity.organization, factory=identity.factories["KTN"]
    )
    await db_session.commit()
    return order.id


@pytest.mark.parametrize(
    ("path", "template"), SCOPED_GET_ROUTES, ids=[p for p, _ in SCOPED_GET_ROUTES]
)
async def test_unauthenticated_caller_gets_401(
    client: AsyncClient, identity: IdentityFixture, path: str, template: str
) -> None:
    ktn = identity.factories["KTN"].id
    url = _fill(template, factory_id=ktn, order_id=uuid.uuid4())
    response = await client.get(url, params=_extra_query(template, factory_id=ktn))
    assert response.status_code == 401, f"{template} -> {response.status_code}, expected 401"
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    ("path", "template"), SCOPED_GET_ROUTES, ids=[p for p, _ in SCOPED_GET_ROUTES]
)
async def test_byg_planner_cannot_reach_ktn_scoped_resources(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
    ktn_order: uuid.UUID,
    path: str,
    template: str,
) -> None:
    byg_planner = await login_as(client, session_factory, "byg.planner@demo.test")
    ktn = identity.factories["KTN"].id
    url = _fill(template, factory_id=ktn, order_id=ktn_order)
    response = await byg_planner.get(url, params=_extra_query(template, factory_id=ktn))
    assert response.status_code == 404, f"{template} -> {response.status_code}, expected 404"
    assert response.json()["error"]["code"] == "NOT_FOUND"
