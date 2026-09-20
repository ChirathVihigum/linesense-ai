#!/usr/bin/env python3
"""Performance smoke test (task-25-brief.md req. 9).

HEAT POLICY: do not run this locally. It drives sustained concurrent load
(default 20 users for 60s) against a real running server and is exactly the
kind of job the machine-heat policy forbids on a laptop; run it only in CI or
with the operator's explicit approval, against the seeded dev database, with
a real `uvicorn` already started (2 workers, no reload -- this script is a
load generator, not the server).

Usage (from services/backend, with the dev DB seeded and a server already
running):

    # Terminal 1: the server under test
    cd services/backend && uv run uvicorn app.main:create_app --factory \\
        --workers 2 --host 127.0.0.1 --port 8000

    # Terminal 2: the load generator
    cd services/backend && uv run python ../../scripts/perf_smoke.py \\
        --base-url http://127.0.0.1:8000 --concurrency 20 --duration 60

What it does:
  1. Creates `--concurrency` real sessions directly in the database
     (`app.auth.sessions.create_session`, documented here as a deliberate
     shortcut around a full OIDC round trip -- this measures API latency,
     not login latency), cycling over the seeded demo identities.
  2. Runs `--concurrency` concurrent workers for `--duration` seconds, each
     looping over GET .../orders, GET .../orders/{id}, GET .../materials,
     GET .../capacity, GET .../dashboard against the seeded demo order/
     factory, recording latency and status per endpoint.
  3. Separately times POST .../orders/{id}/analyses (expected 202) against a
     pool of distinct seeded orders (each order allows only one active run
     at a time), so repeated ack timing does not just measure 409s.
  4. Prints p50/p95/p99 latency and error rate per endpoint, plus the
     analysis-acknowledgement p95, and machine details
     (`sysctl -n machdep.cpu.brand_string`, memory), to stdout and to
     `docs/evaluation/performance.md`-shaped JSON on stdout for pasting in.

Targets from LINESENSE_IMPLEMENTATION_PLAN.md §12 (initial measurable
targets, not guarantees): non-AI API p95 < 500ms at 20 concurrent users;
analysis acknowledgement p95 < 1s.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "services" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.auth.sessions import SESSION_COOKIE, create_session  # noqa: E402
from app.db.models import Factory, Order, User  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.seed.identities import DEMO_IDENTITIES  # noqa: E402
from app.settings import get_settings  # noqa: E402

NON_AI_P95_TARGET_MS = 500.0
ACK_P95_TARGET_MS = 1000.0


@dataclass
class EndpointStats:
    name: str
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    total: int = 0

    def record(self, latency_ms: float, ok: bool) -> None:
        self.total += 1
        if ok:
            self.latencies_ms.append(latency_ms)
        else:
            self.errors += 1

    def percentile(self, p: float) -> float | None:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))
        return ordered[index]

    def error_rate(self) -> float:
        return self.errors / self.total if self.total else 0.0


async def _sessions_for_load(
    session_factory: async_sessionmaker[AsyncSession], count: int
) -> list[tuple[str, str]]:
    """``count`` real ``(session_token, csrf_token)`` pairs, cycling over the
    seeded demo identities (a real OIDC login per worker would measure the
    identity provider, not the API)."""
    pairs: list[tuple[str, str]] = []
    async with session_factory() as session:
        for i in range(count):
            email = DEMO_IDENTITIES[i % len(DEMO_IDENTITIES)][0]
            user = await session.scalar(sa.select(User).where(User.email == email))
            if user is None:
                raise RuntimeError(
                    f"demo identity {email!r} not found; "
                    "run `make seed` against this database first"
                )
            token, record = await create_session(session, user.id)
            pairs.append((token, record.csrf_token))
        await session.commit()
    return pairs


async def _seeded_target(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """``(factory_id, order_id, extra_order_ids)`` from the seeded dataset."""
    async with session_factory() as session:
        factory = await session.scalar(sa.select(Factory).limit(1))
        if factory is None:
            raise RuntimeError(
                "no seeded factory found; run `make seed` against this database first"
            )
        orders = (
            await session.scalars(
                sa.select(Order.id).where(Order.factory_id == factory.id).limit(25)
            )
        ).all()
        if not orders:
            raise RuntimeError(
                "no seeded orders found; run `make seed` against this database first"
            )
        return factory.id, orders[0], list(orders[1:])


async def _timed_get(
    client: httpx.AsyncClient, cookies: dict[str, str], url: str, stats: EndpointStats
) -> None:
    started = time.perf_counter()
    try:
        response = await client.get(url, cookies=cookies, timeout=10.0)
        # A 4xx (auth/scope/validation failure) is a real error for this
        # smoke test's purposes, not a "success" just because it isn't a
        # 5xx -- counting it as ok would understate the error rate task-25
        # req. 9 asks this script to report.
        ok = 200 <= response.status_code < 300
    except httpx.HTTPError:
        ok = False
    stats.record((time.perf_counter() - started) * 1000, ok)


async def _worker(
    base_url: str,
    session_pair: tuple[str, str],
    factory_id: uuid.UUID,
    order_id: uuid.UUID,
    stats_by_endpoint: dict[str, EndpointStats],
    deadline: float,
) -> None:
    token, _csrf = session_pair
    cookies = {SESSION_COOKIE: token}
    urls = {
        "orders_list": f"{base_url}/api/v1/factories/{factory_id}/orders",
        "order_detail": f"{base_url}/api/v1/orders/{order_id}",
        "materials": f"{base_url}/api/v1/factories/{factory_id}/materials",
        "capacity": (
            f"{base_url}/api/v1/factories/{factory_id}/capacity?start=2026-01-01&end=2026-01-31"
        ),
        "dashboard": f"{base_url}/api/v1/factories/{factory_id}/dashboard",
    }
    async with httpx.AsyncClient() as client:
        while time.perf_counter() < deadline:
            for name, url in urls.items():
                if time.perf_counter() >= deadline:
                    break
                await _timed_get(client, cookies, url, stats_by_endpoint[name])


async def _order_versions(
    session_factory: async_sessionmaker[AsyncSession], order_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Each order's real, current ``version`` -- a hardcoded ``1`` would be
    wrong for any seeded order that has already been transitioned at least
    once, making every acknowledgement request 409 ``STALE_INPUT`` instead
    of the 202 this is meant to time (task-25 review round 1, item 7)."""
    async with session_factory() as session:
        rows = (
            await session.execute(sa.select(Order.id, Order.version).where(Order.id.in_(order_ids)))
        ).all()
    return {row.id: row.version for row in rows}


async def _time_analysis_acknowledgements(
    base_url: str,
    session_pair: tuple[str, str],
    order_ids: list[uuid.UUID],
    order_versions: dict[uuid.UUID, int],
    stats: EndpointStats,
) -> None:
    token, csrf = session_pair
    cookies = {SESSION_COOKIE: token}
    headers = {"X-CSRF-Token": csrf, "Origin": base_url}
    async with httpx.AsyncClient() as client:
        for order_id in order_ids:
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{base_url}/api/v1/orders/{order_id}/analyses",
                    json={"expected_order_version": order_versions[order_id]},
                    headers={**headers, "Idempotency-Key": f"perf-{uuid.uuid4().hex}"},
                    cookies=cookies,
                    timeout=10.0,
                )
                ok = response.status_code == 202
            except httpx.HTTPError:
                ok = False
            stats.record((time.perf_counter() - started) * 1000, ok)


SYSCTL = "/usr/sbin/sysctl"


def _machine_info() -> dict[str, str]:
    info = {"platform": platform.platform(), "python": platform.python_version()}
    if sys.platform == "darwin":
        try:
            info["cpu"] = subprocess.check_output(  # noqa: S603
                [SYSCTL, "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            mem_raw = subprocess.check_output([SYSCTL, "-n", "hw.memsize"], text=True)  # noqa: S603
            info["memory_gb"] = f"{int(mem_raw.strip()) / (1024**3):.1f}"
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            pass
    return info


def _print_report(
    stats_by_endpoint: dict[str, EndpointStats], ack_stats: EndpointStats, machine: dict[str, str]
) -> None:
    print("\n=== perf_smoke report ===")
    print("machine:", machine)
    print(f"{'endpoint':<14} {'n':>6} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'err rate':>9}")
    for name, stats in stats_by_endpoint.items():
        p50, p95, p99 = stats.percentile(50), stats.percentile(95), stats.percentile(99)
        print(
            f"{name:<14} {stats.total:>6} "
            f"{p50 or float('nan'):>8.1f} {p95 or float('nan'):>8.1f} {p99 or float('nan'):>8.1f} "
            f"{stats.error_rate():>9.2%}"
        )
        if p95 is not None and p95 > NON_AI_P95_TARGET_MS:
            print(f"  ! p95 {p95:.1f}ms exceeds the {NON_AI_P95_TARGET_MS:.0f}ms target")

    ack_p95 = ack_stats.percentile(95)
    print(
        f"{'analysis_ack':<14} {ack_stats.total:>6} "
        f"{ack_stats.percentile(50) or float('nan'):>8.1f} {ack_p95 or float('nan'):>8.1f} "
        f"{ack_stats.percentile(99) or float('nan'):>8.1f} {ack_stats.error_rate():>9.2%}"
    )
    if ack_p95 is not None and ack_p95 > ACK_P95_TARGET_MS:
        print(f"  ! analysis ack p95 {ack_p95:.1f}ms exceeds the {ACK_P95_TARGET_MS:.0f}ms target")


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    session_factory = get_session_factory(settings.database_url)

    factory_id, primary_order_id, extra_order_ids = await _seeded_target(session_factory)
    sessions = await _sessions_for_load(session_factory, args.concurrency)

    stats_by_endpoint = {
        name: EndpointStats(name)
        for name in ("orders_list", "order_detail", "materials", "capacity", "dashboard")
    }
    deadline = time.perf_counter() + args.duration
    await asyncio.gather(
        *(
            _worker(
                args.base_url,
                sessions[i],
                factory_id,
                primary_order_id,
                stats_by_endpoint,
                deadline,
            )
            for i in range(args.concurrency)
        )
    )

    ack_stats = EndpointStats("analysis_ack")
    ack_order_ids = extra_order_ids[: args.concurrency] or [primary_order_id]
    ack_order_versions = await _order_versions(session_factory, ack_order_ids)
    await _time_analysis_acknowledgements(
        args.base_url, sessions[0], ack_order_ids, ack_order_versions, ack_stats
    )

    _print_report(stats_by_endpoint, ack_stats, _machine_info())
    await get_engine(settings.database_url).dispose()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=60.0, help="seconds")
    return parser.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(_main(_parse_args(sys.argv[1:])))
