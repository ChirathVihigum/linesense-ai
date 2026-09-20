"""``python -m app.evaluation [--embedder fastembed|hashing] [--output-dir DIR] [--scenarios N]``

Runs every evaluation section against a dedicated database (never
``linesense_dev``/``linesense_test``) and writes a versioned JSON report
plus a Markdown rendering under ``docs/evaluation/results/``. Exit code 0
even when a target is missed (the report shows it); exit code 2 only on a
harness error (unreachable database, missing dataset file, ...). See
``docs/evaluation/methodology.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa

from app.agents.registry import AGENTS
from app.db.models import Factory
from app.db.session import get_session_factory
from app.domain.clock import utcnow
from app.evaluation.agent_eval import RNG_SEED, run_agent_eval
from app.evaluation.calc_eval import run_calc_eval
from app.evaluation.fairness_eval import run_fairness_eval
from app.evaluation.nlp_eval import (
    NOTES_TEST_PATH,
    build_eval_master_data,
    run_classification_eval,
    run_entity_eval,
)
from app.evaluation.report import build_report, sha256_directory, sha256_file, write_report
from app.evaluation.retrieval_eval import QUESTIONS_PATH, run_retrieval_eval
from app.evaluation.security_eval import run_security_eval
from app.llm.fixture_client import FixtureLLMClient
from app.main import create_app
from app.nlp.classifier import training_file_version
from app.retrieval.embedder import build_embedder
from app.retrieval.loader import load_corpus_directory
from app.retrieval.search import RetrievalScope
from app.retrieval.storage import DocumentStorage
from app.seed.generator import SeedSummary, seed_demo
from app.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parents[1]
SOPS_DIR = REPO_ROOT / "data" / "synthetic" / "sops"

DEFAULT_EVAL_DATABASE_URL = (
    "postgresql+psycopg://linesense_app:dev-app-only@127.0.0.1:55432/linesense_eval"
)
DEFAULT_EVAL_MIGRATION_DATABASE_URL = (
    "postgresql+psycopg://linesense_owner:dev-owner-only@127.0.0.1:55432/linesense_eval"
)
ANCHOR_DATE = date(2026, 9, 17)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    parser.add_argument("--embedder", choices=["fastembed", "hashing"], default="fastembed")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "docs" / "evaluation" / "results"))
    parser.add_argument("--scenarios", type=int, default=12)
    return parser.parse_args(argv)


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-x", f"db_url={db_url}", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def _reset_database(migration_url: str) -> None:
    """Bring the eval database's schema to a known, empty state.

    When the target is the real ``linesense_eval`` database, this also
    drops and recreates it via ``scripts/dev-db.sh reset-eval`` first
    (task-19-brief.md requirement 1). When ``LS_EVAL_DATABASE_URL`` has
    been pointed at something else -- e.g. an isolated per-agent test
    database, as this task's own smoke run does to avoid provisioning a
    new shared database while other agents are working in the same
    cluster -- only the alembic downgrade/upgrade dance runs, exactly as
    ``tests/conftest.py``'s ``migrated_db`` fixture does for every
    integration test.
    """
    db_name = migration_url.rsplit("/", 1)[-1]
    if db_name == "linesense_eval":
        subprocess.run(  # noqa: S603
            ["bash", str(REPO_ROOT / "scripts" / "dev-db.sh"), "reset-eval"],  # noqa: S607
            check=True,
        )
    down = _run_alembic(migration_url, "downgrade", "base")
    if down.returncode != 0:
        raise RuntimeError(f"alembic downgrade base failed:\n{down.stdout}{down.stderr}")
    up = _run_alembic(migration_url, "upgrade", "head")
    if up.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{up.stdout}{up.stderr}")


async def _seed_and_load_corpus(
    settings: Settings, *, document_storage_dir: Path
) -> tuple[SeedSummary, dict[str, object]]:
    session_factory = get_session_factory(settings.database_url)
    async with session_factory() as session:
        summary = await seed_demo(session, anchor_date=ANCHOR_DATE, issuer=settings.oidc_issuer)
        await session.commit()

    embedder = build_embedder(settings)
    storage = DocumentStorage(document_storage_dir)
    version_ids = await load_corpus_directory(
        session_factory,
        organization_id=summary.organization_id,
        directory=SOPS_DIR,
        embedder=embedder,
        storage=storage,
        created_by=None,
    )
    return summary, {"versions_created": len(version_ids)}


async def _factory_ids(settings: Settings, organization_id: uuid.UUID) -> dict[str, uuid.UUID]:
    session_factory = get_session_factory(settings.database_url)
    async with session_factory() as session:
        rows = (
            await session.execute(
                sa.select(Factory.code, Factory.id).where(
                    Factory.organization_id == organization_id
                )
            )
        ).all()
    return {code: factory_id for code, factory_id in rows}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    eval_database_url = os.environ.get("LS_EVAL_DATABASE_URL", DEFAULT_EVAL_DATABASE_URL)
    eval_migration_database_url = os.environ.get(
        "LS_EVAL_MIGRATION_DATABASE_URL", DEFAULT_EVAL_MIGRATION_DATABASE_URL
    )

    _reset_database(eval_migration_database_url)

    document_storage_dir = Path(tempfile.mkdtemp(prefix="linesense-eval-documents-"))
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=eval_database_url,
        migration_database_url=eval_migration_database_url,
        test_database_url=eval_database_url,
        test_migration_database_url=eval_migration_database_url,
        embedder=args.embedder,
        llm_provider="fixture",
        document_storage_dir=str(document_storage_dir),
    )

    summary, corpus_load = await _seed_and_load_corpus(
        settings, document_storage_dir=document_storage_dir
    )
    factories = await _factory_ids(settings, summary.organization_id)
    ktn_id = factories["KTN"]
    byg_id = factories["BYG"]

    session_factory = get_session_factory(settings.database_url)
    embedder = build_embedder(settings)
    scope = RetrievalScope(
        organization_id=summary.organization_id, factory_id=ktn_id, roles=frozenset({"supervisor"})
    )
    retrieval_result = await run_retrieval_eval(session_factory, scope, embedder)

    async with session_factory() as session:
        master = await build_eval_master_data(
            session, organization_id=summary.organization_id, factory_ids=[ktn_id, byg_id]
        )
    entities_result = run_entity_eval(master)
    classification_result = run_classification_eval()
    calc_result = run_calc_eval()

    app_instance = create_app(settings)
    transport = httpx.ASGITransport(app=app_instance, raise_app_exceptions=False)
    agents_result = await run_agent_eval(
        session_factory, settings, transport, scenarios=args.scenarios
    )
    fairness_pairs = min(10, max(1, args.scenarios))
    fairness_result = await run_fairness_eval(
        session_factory, settings, transport, pairs=fairness_pairs
    )

    security_env = os.environ.copy()
    security_env["LS_TEST_DATABASE_URL"] = eval_database_url
    security_env["LS_TEST_MIGRATION_DATABASE_URL"] = eval_migration_database_url
    security_result = run_security_eval(security_env)

    report = build_report(
        embedder_name=embedder.model_name,
        classifier_version=training_file_version(),
        prompt_versions={name: cls.prompt_version for name, cls in AGENTS.items()},
        llm_provider="fixture",
        llm_model=FixtureLLMClient.model,
        corpus_sha256=sha256_directory(SOPS_DIR),
        notes_test_sha256=sha256_file(NOTES_TEST_PATH),
        questions_sha256=sha256_file(QUESTIONS_PATH),
        seed=RNG_SEED,
        retrieval=retrieval_result,
        entities=entities_result,
        classification=classification_result,
        calculations=calc_result,
        agents=agents_result,
        fairness=fairness_result,
        security=security_result,
        repo_root=REPO_ROOT,
    )
    report["corpus_load"] = corpus_load
    report["ran_at_utc"] = utcnow().isoformat()
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - a harness error, never a fabricated result
        print(f"evaluation harness error: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (BACKEND_DIR / output_dir).resolve()
    timestamped_path, latest_json_path, latest_md_path = write_report(report, output_dir)

    print(f"wrote {timestamped_path}")
    print(f"wrote {latest_json_path}")
    print(f"wrote {latest_md_path}")
    for name, result in report["target_results"].items():
        status = "OK" if result["met"] else "MISSED"
        print(f"[{status}] {name}: target={result['target']} actual={result['actual']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
