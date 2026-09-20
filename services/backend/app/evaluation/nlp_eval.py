"""Entities and classification sections (task-19-brief.md requirements 3-4).

Both sections run against ``data/eval/notes_test.jsonl`` (never the train
split — see ``data/eval/README.md``'s train/test separation rule).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation import metrics
from app.nlp.classifier import CLASSES, KeywordBaselineClassifier, TfidfNoteClassifier
from app.nlp.entities import EntityExtractor, MasterData, load_master_data

REPO_ROOT = Path(__file__).resolve().parents[4]
NOTES_TRAIN_PATH = REPO_ROOT / "data" / "eval" / "notes_train.jsonl"
NOTES_TEST_PATH = REPO_ROOT / "data" / "eval" / "notes_test.jsonl"

_ENTITY_MISS_LIMIT = 25


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _merge_master_data(parts: list[MasterData]) -> MasterData:
    """Union several factories' master data (entities test notes reference
    orders/lines across both demo factories, but ``load_master_data`` is
    intentionally factory-scoped for production use — see
    ``app.nlp.entities`` requirement 3). Styles/materials/operations/
    defects are identical across factories already; only orders/lines
    differ.
    """
    orders: dict[str, Any] = {}
    lines: dict[str, Any] = {}
    for part in parts:
        orders.update(part.orders)
        lines.update(part.lines)
    base = parts[0]
    return MasterData(
        orders=orders,
        lines=lines,
        styles=base.styles,
        materials=base.materials,
        operations=base.operations,
        defects=base.defects,
    )


async def build_eval_master_data(
    session: AsyncSession, *, organization_id: Any, factory_ids: list[Any]
) -> MasterData:
    parts = [
        await load_master_data(session, organization_id, factory_id) for factory_id in factory_ids
    ]
    return _merge_master_data(parts)


def _entity_tuples(entities: list[dict[str, Any]]) -> set[tuple[str, int, int]]:
    return {(entity["label"], entity["start"], entity["end"]) for entity in entities}


def run_entity_eval(
    master: MasterData, notes: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Micro precision/recall/F1 over exact ``(label, start, end)`` matches
    (task-19-brief.md requirement 3): target micro-F1 >= 0.90.
    """
    rows = notes if notes is not None else _load_jsonl(NOTES_TEST_PATH)
    extractor = EntityExtractor(master)

    true_sets: list[set[tuple[str, int, int]]] = []
    pred_sets: list[set[tuple[str, int, int]]] = []
    misses: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []

    for row in rows:
        true_set = _entity_tuples(row["entities"])
        mentions = extractor.extract(row["text"])
        pred_set = {(m.label, m.start, m.end) for m in mentions}
        true_sets.append(true_set)
        pred_sets.append(pred_set)

        for label, start, end in sorted(true_set - pred_set):
            if len(misses) < _ENTITY_MISS_LIMIT:
                misses.append(
                    {"note_id": row["id"], "label": label, "text": row["text"][start:end]}
                )
        for label, start, end in sorted(pred_set - true_set):
            if len(false_positives) < _ENTITY_MISS_LIMIT:
                false_positives.append(
                    {"note_id": row["id"], "label": label, "text": row["text"][start:end]}
                )

    precision, recall, f1 = metrics.micro_prf(true_sets, pred_sets)
    per_label = metrics.per_label_prf(true_sets, pred_sets)

    return {
        "notes_evaluated": len(rows),
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": f1,
        "per_label": per_label,
        "misses": misses,
        "false_positives": false_positives,
        "misses_truncated": len({(m["note_id"], m["label"]) for m in misses}) >= _ENTITY_MISS_LIMIT,
    }


def _predict_tfidf(classifier: TfidfNoteClassifier, texts: list[str]) -> list[str]:
    # Plain `.predict()`, not `.predict_with_margin()`: the margin threshold is a
    # production abstention behaviour (fall back to "unknown" below a confidence
    # cutoff tuned for live note classification), not what this section measures.
    # This section measures the trained model's raw classification ability.
    return classifier.predict(texts)


def run_classification_eval(
    train_path: Path = NOTES_TRAIN_PATH, test_path: Path = NOTES_TEST_PATH
) -> dict[str, Any]:
    """Keyword baseline and TF-IDF model on the test set (task-19-brief.md
    requirement 4): target macro-F1 >= 0.80 for TF-IDF.
    """
    train_rows = _load_jsonl(train_path)
    test_rows = _load_jsonl(test_path)
    train_examples = [(row["text"], row["label"]) for row in train_rows]
    test_texts = [row["text"] for row in test_rows]
    y_true = [row["label"] for row in test_rows]

    keyword_pred = KeywordBaselineClassifier().predict(test_texts)
    tfidf_model = TfidfNoteClassifier.train(train_examples)
    tfidf_pred = _predict_tfidf(tfidf_model, test_texts)

    def _summarize(y_pred: list[str]) -> dict[str, Any]:
        support = {label: y_true.count(label) for label in CLASSES}
        return {
            "accuracy": metrics.accuracy(y_true, y_pred),
            "macro_f1": metrics.macro_f1(y_true, y_pred, labels=list(CLASSES)),
            "support": support,
            "confusion_matrix": metrics.confusion_matrix(y_true, y_pred, labels=list(CLASSES)),
        }

    return {
        "examples_evaluated": len(test_rows),
        "keyword_baseline": _summarize(keyword_pred),
        "tfidf": _summarize(tfidf_pred),
    }
