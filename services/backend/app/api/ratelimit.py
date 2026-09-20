"""Single-process, per-instance token-bucket rate limiting (task-25-brief.md req. 2).

Buckets are matched by (method, path-regex) against the resolved ASGI path,
so this needs no changes to the route modules it protects (several of which
other in-flight tasks own concurrently). State lives entirely in this
process's memory: behind more than one worker/instance each enforces its own
independent budget, so the effective aggregate limit scales with instance
count. That is a deliberate, documented limitation (see
``docs/security/threat-model.md``) -- a shared store (e.g. Redis) would be
needed to cap the *aggregate* rate across instances, and is out of scope
here.

Disabled by default whenever ``Settings.environment == "test"`` (see
:func:`rate_limiting_enabled`), so it never trips ordinary test runs; a test
that wants to exercise it passes ``rate_limit_enabled=True`` explicitly.
"""

from __future__ import annotations

import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.csrf import STATE_RECORD, STATE_RESOLVED
from app.auth.sessions import SESSION_COOKIE, resolve_session
from app.db.models import SessionRecord
from app.db.session import get_session_factory
from app.settings import Settings

RATE_LIMITED_CODE = "RATE_LIMITED"
RATE_LIMITED_MESSAGE = "Too many requests. Please slow down and try again shortly."


@dataclass(frozen=True)
class RateLimitBucket:
    name: str
    methods: frozenset[str]
    path_re: re.Pattern[str]
    limit: int
    window_seconds: float
    keyed_by: str  # "user" or "ip"

    @property
    def refill_per_second(self) -> float:
        return self.limit / self.window_seconds


# backend-contracts.md section 5 paths; matched against the *resolved* ASGI
# path, so a router's own prefix (or lack of one) never matters here.
BUCKETS: tuple[RateLimitBucket, ...] = (
    RateLimitBucket(
        "analysis_create",
        frozenset({"POST"}),
        re.compile(r"^/api/v1/orders/[^/]+/analyses$"),
        limit=10,
        window_seconds=60.0,
        keyed_by="user",
    ),
    RateLimitBucket(
        "document_upload",
        frozenset({"POST"}),
        re.compile(r"^/api/v1/factories/[^/]+/documents$"),
        limit=10,
        window_seconds=600.0,
        keyed_by="user",
    ),
    RateLimitBucket(
        "import_upload",
        frozenset({"POST"}),
        re.compile(r"^/api/v1/factories/[^/]+/imports/[^/]+$"),
        limit=10,
        window_seconds=600.0,
        keyed_by="user",
    ),
    RateLimitBucket(
        "search",
        frozenset({"GET"}),
        re.compile(r"^/api/v1/factories/[^/]+/search$"),
        limit=60,
        window_seconds=60.0,
        keyed_by="user",
    ),
    RateLimitBucket(
        "note_create",
        frozenset({"POST"}),
        re.compile(r"^/api/v1/factories/[^/]+/notes$"),
        limit=30,
        window_seconds=60.0,
        keyed_by="user",
    ),
    RateLimitBucket(
        "auth_login",
        frozenset({"GET"}),
        re.compile(r"^/auth/login$"),
        limit=20,
        window_seconds=60.0,
        keyed_by="ip",
    ),
)


def rate_limiting_enabled(settings: Settings) -> bool:
    if settings.rate_limit_enabled is not None:
        return settings.rate_limit_enabled
    return settings.environment != "test"


def _match_bucket(path: str, method: str) -> RateLimitBucket | None:
    for bucket in BUCKETS:
        if method in bucket.methods and bucket.path_re.match(path):
            return bucket
    return None


DEFAULT_MAX_ENTRIES = 50_000
"""Eviction cap (task-25 review round 1, item 9): the IP-keyed `auth_login`
bucket in particular has no natural bound on distinct identities (any
number of source IPs can each get their own entry), so left unbounded this
dict would grow forever under sustained traffic from many distinct
addresses -- a slow memory-exhaustion DoS against the rate limiter itself.
50,000 entries is generously above any real single-instance concurrent
identity count and small in memory (a few MB of tuples)."""


class TokenBucketLimiter:
    """In-memory token buckets keyed by ``(bucket name, identity)``, bounded
    to ``max_entries`` with least-recently-used eviction.

    No lock: every call happens synchronously (no ``await`` between reading
    and writing a bucket's state), and a single asyncio event loop never runs
    two coroutines' Python bytecode concurrently, so this is safe under
    ``--concurrency`` > 1 workers *within one process* as long as they share
    one event loop (uvicorn's default).
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._clock = clock
        self._max_entries = max_entries
        # An OrderedDict used as an LRU: every access (read or write) via
        # `try_consume` moves that key to the end; the oldest (least
        # recently used) entries are the ones evicted once the cap is hit,
        # regardless of whether their bucket happens to be full or empty.
        self._state: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()

    def try_consume(self, bucket: RateLimitBucket, identity: str) -> float | None:
        """``None`` if a token was consumed (request allowed); otherwise the
        number of seconds until a token will be available."""
        key = (bucket.name, identity)
        now = self._clock()
        tokens, last = self._state.get(key, (float(bucket.limit), now))
        elapsed = max(0.0, now - last)
        tokens = min(float(bucket.limit), tokens + elapsed * bucket.refill_per_second)
        if tokens >= 1.0:
            self._state[key] = (tokens - 1.0, now)
            self._state.move_to_end(key)
            self._evict_if_over_capacity()
            return None
        self._state[key] = (tokens, now)
        self._state.move_to_end(key)
        self._evict_if_over_capacity()
        return (1.0 - tokens) / bucket.refill_per_second

    def _evict_if_over_capacity(self) -> None:
        while len(self._state) > self._max_entries:
            self._state.popitem(last=False)

    def reset(self) -> None:
        """Test helper: clear all bucket state."""
        self._state.clear()


def _rate_limited_response(*, trace_id: str, retry_after_seconds: int) -> JSONResponse:
    body = {
        "error": {
            "code": RATE_LIMITED_CODE,
            "message": RATE_LIMITED_MESSAGE,
            "field_errors": [],
            "trace_id": trace_id,
            "retry_after_seconds": retry_after_seconds,
        }
    }
    return JSONResponse(
        status_code=429, content=body, headers={"Retry-After": str(retry_after_seconds)}
    )


def _session_cookie(headers: list[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name.lower() == b"cookie":
            token = cookie_parser(value.decode("latin-1")).get(SESSION_COOKIE)
            if token:
                return token
    return None


class RateLimitMiddleware:
    """Enforces :data:`BUCKETS` against every matching request.

    Added *before* ``CsrfMiddleware`` in ``app.main.create_app`` (Starlette
    runs the most recently added middleware first -- see that module's
    ordering comment), so this runs immediately before the route itself, once
    ``CsrfMiddleware`` has already resolved the session for any unsafe
    ``/api/...`` request; this reuses that resolution instead of hitting the
    database twice. A safe-method request (e.g. the ``search`` bucket's GET)
    is never touched by ``CsrfMiddleware``, so this resolves the session
    itself in that case.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: TokenBucketLimiter | None = None,
    ) -> None:
        self.app = app
        self.enabled = rate_limiting_enabled(settings)
        self.database_url = settings.database_url
        self.limiter = limiter or TokenBucketLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        bucket = _match_bucket(scope["path"], scope["method"])
        if bucket is None:
            await self.app(scope, receive, send)
            return

        identity = await self._identity(scope, bucket)
        if identity is None:
            # No live session (bucket keyed by user) or no client address
            # (keyed by IP): nothing to key a budget on. Let the route's own
            # auth dependency answer (401 for an anonymous caller); it would
            # be wrong to either block everyone into one shared bucket or to
            # silently exempt them from a check meant to gate authenticated
            # or origin-identified traffic.
            await self.app(scope, receive, send)
            return

        wait_seconds = self.limiter.try_consume(bucket, identity)
        if wait_seconds is None:
            await self.app(scope, receive, send)
            return

        trace_id = str(scope.get("state", {}).get("trace_id", ""))
        response = _rate_limited_response(
            trace_id=trace_id, retry_after_seconds=max(1, math.ceil(wait_seconds))
        )
        await response(scope, receive, send)

    async def _identity(self, scope: Scope, bucket: RateLimitBucket) -> str | None:
        if bucket.keyed_by == "ip":
            client = scope.get("client")
            return client[0] if client else "unknown"

        state = scope.setdefault("state", {})
        if state.get(STATE_RESOLVED):
            record: SessionRecord | None = state.get(STATE_RECORD)
        else:
            record = await self._resolve_session(scope)
            state[STATE_RESOLVED] = True
            state[STATE_RECORD] = record
        return str(record.user_id) if record is not None else None

    async def _resolve_session(self, scope: Scope) -> SessionRecord | None:
        headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
        token = _session_cookie(headers)
        if not token:
            return None
        async with get_session_factory(self.database_url)() as session:
            record = await resolve_session(session, token)
            await session.commit()
        return record
