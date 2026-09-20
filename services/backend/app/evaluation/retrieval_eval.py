"""Retrieval section (task-19-brief.md requirement 2).

For each **test**-split question in ``data/eval/retrieval_questions.jsonl``,
compute Recall@5 (a hit if any of the top-5 chunks match a relevant
``(doc_slug, section)`` pair) and MRR@10, for ``lexical``, ``vector`` and
``hybrid`` search, all under a fixed KTN-supervisor scope. Two of the
test-split questions target a BYG-only document (``scope_factory: "BYG"``)
and are expected to miss under this scope by design (access control, not a
retrieval defect) — see ``docs/evaluation/methodology.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evaluation import metrics
from app.retrieval.embedder import Embedder
from app.retrieval.search import RetrievalScope, SearchMode, search

REPO_ROOT = Path(__file__).resolve().parents[4]
QUESTIONS_PATH = REPO_ROOT / "data" / "eval" / "retrieval_questions.jsonl"

MODES: tuple[SearchMode, ...] = ("lexical", "vector", "hybrid")
RECALL_K = 5
MRR_K = 10


def _load_questions(path: Path = QUESTIONS_PATH, *, split: str = "test") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row["split"] == split:
                rows.append(row)
    return rows


def _target_keys(question: dict[str, Any]) -> set[tuple[str, str]]:
    return {(r["doc_slug"], r["section"]) for r in question["relevant"]}


async def run_retrieval_eval(
    session_factory: async_sessionmaker[AsyncSession],
    scope: RetrievalScope,
    embedder: Embedder,
    *,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = questions if questions is not None else _load_questions()

    per_mode: dict[str, Any] = {}
    for mode in MODES:
        retrieved_keys: list[list[tuple[str, str]]] = []
        per_question: list[dict[str, Any]] = []
        for row in rows:
            async with session_factory() as session:
                chunks = await search(
                    session, scope, row["question"], k=MRR_K, mode=mode, embedder=embedder
                )
            keys = [(chunk.document_slug, chunk.section or "") for chunk in chunks]
            retrieved_keys.append(keys)
            target = _target_keys(row)
            hit_at_5 = any(key in target for key in keys[:RECALL_K])
            first_rank = next((i + 1 for i, key in enumerate(keys) if key in target), None)
            per_question.append(
                {
                    "id": row["id"],
                    "hit_at_5": hit_at_5,
                    "first_relevant_rank": first_rank,
                    "scope_factory": row["scope_factory"],
                }
            )

        relevant = [_target_keys(row) for row in rows]
        recall5 = metrics.recall_at_k(retrieved_keys, relevant, k=RECALL_K)
        mrr10 = metrics.mrr(retrieved_keys, relevant, k=MRR_K)
        failures = [
            {"id": q["id"], "scope_factory": q["scope_factory"]}
            for q in per_question
            if not q["hit_at_5"]
        ]
        per_mode[mode] = {
            "recall_at_5": recall5,
            "mrr_at_10": mrr10,
            "per_question": per_question,
            "failures": failures,
        }

    return {
        "questions_evaluated": len(rows),
        "modes": per_mode,
        "scope": {
            "organization_id": str(scope.organization_id),
            "factory_id": str(scope.factory_id),
            "roles": sorted(scope.roles),
        },
    }
