"""``python -m app.seed [--anchor-date YYYY-MM-DD]``.

Seeds the deterministic synthetic demonstration dataset (Task 6) against
``LS_DATABASE_URL``. Refuses to run when ``LS_ENVIRONMENT=production``
(exit code 2). Never deletes or truncates data; safe to run repeatedly
(idempotent — a second run is a no-op). Prints the resulting
``SeedSummary`` as JSON on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.db.session import get_session_factory
from app.seed.generator import SeedSummary, seed_demo
from app.settings import get_settings


def _default_anchor_date() -> date:
    return datetime.now(ZoneInfo("Asia/Colombo")).date()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.seed")
    parser.add_argument(
        "--anchor-date",
        type=date.fromisoformat,
        default=None,
        help="Anchor date (YYYY-MM-DD); defaults to today in Asia/Colombo.",
    )
    return parser.parse_args(argv)


def _summary_to_json(summary: SeedSummary) -> dict[str, object]:
    payload = asdict(summary)
    payload["organization_id"] = str(summary.organization_id)
    payload["demo_order_id"] = str(summary.demo_order_id) if summary.demo_order_id else None
    return payload


async def _run(anchor_date: date) -> SeedSummary:
    settings = get_settings()
    session_factory = get_session_factory(settings.database_url)
    async with session_factory() as session:
        summary = await seed_demo(session, anchor_date=anchor_date, issuer=settings.oidc_issuer)
        await session.commit()
    return summary


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    if settings.environment == "production":
        print("refusing to seed: LS_ENVIRONMENT=production", file=sys.stderr)
        return 2

    args = _parse_args(argv)
    anchor_date = args.anchor_date or _default_anchor_date()
    summary = asyncio.run(_run(anchor_date))
    print(json.dumps(_summary_to_json(summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
