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
import uuid
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Membership, User
from app.db.models.documents import Chunk
from app.db.session import get_session_factory
from app.retrieval.embedder import build_embedder
from app.retrieval.loader import load_corpus_directory
from app.retrieval.storage import DocumentStorage
from app.seed.generator import SeedSummary, seed_demo
from app.settings import get_settings, resolve_backend_path

# services/backend/app/seed/__main__.py -> repo root (5 levels up).
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CORPUS_DIR = _REPO_ROOT / "data" / "synthetic" / "sops"
_ADMIN_EMAIL = "admin@demo.test"


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
    parser.add_argument(
        "--with-documents",
        action="store_true",
        help=(
            "Also load the synthetic SOP corpus (data/synthetic/sops) through the "
            "document pipeline. Idempotent by (slug, sha256). Uses LS_EMBEDDER "
            "(fastembed by default); the first run downloads the embedding model."
        ),
    )
    return parser.parse_args(argv)


def _summary_to_json(summary: SeedSummary) -> dict[str, object]:
    payload = asdict(summary)
    payload["organization_id"] = str(summary.organization_id)
    payload["demo_order_id"] = str(summary.demo_order_id) if summary.demo_order_id else None
    return payload


async def _admin_user_id(
    session_factory: async_sessionmaker[AsyncSession], organization_id: uuid.UUID
) -> uuid.UUID | None:
    async with session_factory() as session:
        user_id: uuid.UUID | None = await session.scalar(
            select(User.id)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.organization_id == organization_id, User.email == _ADMIN_EMAIL)
        )
    return user_id


async def _count_chunks(
    session_factory: async_sessionmaker[AsyncSession], version_ids: list[uuid.UUID]
) -> int:
    if not version_ids:
        return 0
    async with session_factory() as session:
        count = await session.scalar(
            select(sa.func.count())
            .select_from(Chunk)
            .where(Chunk.document_version_id.in_(version_ids))
        )
    return int(count or 0)


async def _load_documents(
    session_factory: async_sessionmaker[AsyncSession], *, organization_id: uuid.UUID
) -> dict[str, int]:
    settings = get_settings()
    admin_user_id = await _admin_user_id(session_factory, organization_id)
    embedder = build_embedder(settings)
    storage = DocumentStorage(resolve_backend_path(settings.document_storage_dir))
    version_ids = await load_corpus_directory(
        session_factory,
        organization_id=organization_id,
        directory=_CORPUS_DIR,
        embedder=embedder,
        storage=storage,
        created_by=admin_user_id,
    )
    chunk_count = await _count_chunks(session_factory, version_ids)
    return {"versions_created": len(version_ids), "total_chunks": chunk_count}


async def _run(
    anchor_date: date, *, with_documents: bool
) -> tuple[SeedSummary, dict[str, int] | None]:
    settings = get_settings()
    session_factory = get_session_factory(settings.database_url)
    async with session_factory() as session:
        summary = await seed_demo(session, anchor_date=anchor_date, issuer=settings.oidc_issuer)
        await session.commit()

    documents_summary: dict[str, int] | None = None
    if with_documents:
        documents_summary = await _load_documents(
            session_factory, organization_id=summary.organization_id
        )
    return summary, documents_summary


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    if settings.environment == "production":
        print("refusing to seed: LS_ENVIRONMENT=production", file=sys.stderr)
        return 2

    args = _parse_args(argv)
    anchor_date = args.anchor_date or _default_anchor_date()
    if sys.platform == "win32":
        import selectors
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            summary, documents_summary = loop.run_until_complete(
                _run(anchor_date, with_documents=args.with_documents)
            )
        finally:
            loop.close()
    else:
        summary, documents_summary = asyncio.run(_run(anchor_date, with_documents=args.with_documents))
    payload = _summary_to_json(summary)
    if documents_summary is not None:
        payload["documents"] = documents_summary
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
