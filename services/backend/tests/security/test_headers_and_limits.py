"""Security headers and per-instance rate limiting (task-25-brief.md req. 1-2).

Header checks and the token-bucket unit need no database; the per-user
bucket end-to-end check does (a real login), so only that one is marked
``integration``.
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.ratelimit import BUCKETS, RateLimitBucket, TokenBucketLimiter, rate_limiting_enabled
from app.main import create_app
from app.settings import Settings
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.security


def _prod_settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "environment": "production",
        "session_secret": "a-very-long-and-safe-session-secret-0123456789",
        "service_token": "a-very-long-and-safe-service-token-0123456789",
        "public_origin": "https://app.linesense.example.com",
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-live-example-key",
        "oidc_issuer": "https://login.example.com/realms/linesense",
        "oidc_client_secret": "a-real-client-secret-from-the-idp-0123",
        "oidc_redirect_uri": "https://app.linesense.example.com/auth/callback",
    }
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


async def _get(settings: Settings, path: str) -> object:
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


# --------------------------------------------------------------------------
# Headers
# --------------------------------------------------------------------------


async def test_api_json_responses_get_a_locked_down_csp_and_permissions_policy() -> None:
    response = await _get(Settings(_env_file=None, environment="test"), "/api/health/live")
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["permissions-policy"]
    assert "strict-transport-security" not in response.headers


async def test_hsts_is_only_sent_in_production() -> None:
    response = await _get(_prod_settings(), "/api/health/live")
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


# --------------------------------------------------------------------------
# Rate limiting: enable/disable rule and the token bucket itself
# --------------------------------------------------------------------------


def test_rate_limiting_is_off_by_default_in_tests_and_on_elsewhere() -> None:
    assert rate_limiting_enabled(Settings(_env_file=None, environment="test")) is False
    assert rate_limiting_enabled(Settings(_env_file=None, environment="development")) is True
    assert rate_limiting_enabled(_prod_settings()) is True
    assert (
        rate_limiting_enabled(Settings(_env_file=None, environment="test", rate_limit_enabled=True))
        is True
    )
    assert rate_limiting_enabled(_prod_settings(rate_limit_enabled=False)) is False


def test_token_bucket_limiter_allows_up_to_the_limit_then_blocks_then_refills() -> None:
    clock = {"t": 0.0}
    limiter = TokenBucketLimiter(clock=lambda: clock["t"])
    bucket = RateLimitBucket(
        "unit-test",
        frozenset({"GET"}),
        re.compile(r"^/x$"),
        limit=2,
        window_seconds=10.0,
        keyed_by="ip",
    )

    assert limiter.try_consume(bucket, "1.2.3.4") is None
    assert limiter.try_consume(bucket, "1.2.3.4") is None
    wait = limiter.try_consume(bucket, "1.2.3.4")
    assert wait == pytest.approx(5.0)

    # A different identity has its own, independent budget.
    assert limiter.try_consume(bucket, "9.9.9.9") is None

    clock["t"] += 5.0
    assert limiter.try_consume(bucket, "1.2.3.4") is None


async def test_auth_login_is_rate_limited_per_ip_with_retry_after() -> None:
    # `/auth/login`'s handler itself talks to the (unreachable, in tests) dev
    # OIDC issuer; `raise_app_exceptions=False` turns that into a normal (if
    # unrelated) error response instead of failing the test, since only the
    # *rate limiter's own* 429 is under test here, not the login flow.
    login_bucket = next(bucket for bucket in BUCKETS if bucket.name == "auth_login")
    settings = Settings(_env_file=None, environment="test", rate_limit_enabled=True)
    transport = ASGITransport(app=create_app(settings), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        statuses = []
        for _ in range(login_bucket.limit + 1):
            response = await client.get("/auth/login")
            statuses.append(response.status_code)
        assert statuses[: login_bucket.limit].count(429) == 0
        assert statuses[-1] == 429
        body = response.json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert isinstance(body["error"]["retry_after_seconds"], int)
        assert body["error"]["retry_after_seconds"] > 0
        assert "Retry-After" in response.headers


# --------------------------------------------------------------------------
# End-to-end: a real per-user bucket through a real login (needs the database)
# --------------------------------------------------------------------------


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


@pytest.mark.integration
async def test_search_is_rate_limited_per_user(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    search_bucket = next(bucket for bucket in BUCKETS if bucket.name == "search")
    rate_limited_settings = settings.model_copy(update={"rate_limit_enabled": True})
    transport = ASGITransport(app=create_app(rate_limited_settings))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        planner = await login_as(client, session_factory, "planner@demo.test")
        ktn = identity.factories["KTN"]

        statuses = []
        for _ in range(search_bucket.limit + 1):
            response = await planner.get(f"/api/v1/factories/{ktn.id}/search", params={"q": "sop"})
            statuses.append(response.status_code)

        assert 429 not in statuses[: search_bucket.limit]
        assert statuses[-1] == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"

        # A different user gets an independent budget.
        other = await login_as(client, session_factory, "supervisor@demo.test")
        other_response = await other.get(f"/api/v1/factories/{ktn.id}/search", params={"q": "sop"})
        assert other_response.status_code != 429
