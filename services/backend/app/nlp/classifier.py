"""Note classification: a fixed-keyword baseline and a trained TF-IDF +
logistic-regression classifier (task-18-brief.md requirement 2).

Labels: ``planning``, ``materials``, ``ie``, ``quality``, ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

CLASSES: tuple[str, ...] = ("planning", "materials", "ie", "quality", "unknown")

#: Low-margin threshold: `predict_with_margin` returns "unknown" when the
#: winning class's predicted probability is below this.
MARGIN_THRESHOLD = 0.45

_REPO_ROOT = Path(__file__).resolve().parents[4]
TRAIN_DATASET_PATH = _REPO_ROOT / "data" / "eval" / "notes_train.jsonl"

# Fixed keyword lists per class (documented, task-18-brief.md requirement 2).
# Matching is a case-insensitive substring search; a note is scored by how
# many distinct keywords from each class's list appear in it, and the
# highest-scoring class wins (ties broken by `CLASSES` order). A note that
# matches no keyword from any class is "unknown".
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "planning": (
        "schedule",
        "scheduled",
        "scheduling",
        "slot",
        "sequence",
        "sequencing",
        "loading board",
        "changeover",
        "queue",
        "due date",
        "priority",
        "pulled forward",
        "committed",
        "committing",
        "capacity plan",
        "split across",
        "loading",
        "planner",
        "unscheduled",
    ),
    "materials": (
        "material",
        "materials",
        "fabric",
        "thread",
        "stock",
        "shortage",
        "reorder",
        "receipt",
        "received",
        "reservation",
        "reserved",
        "top-up",
        "topped up",
        "lot",
        "store",
        "storekeeper",
        "supplier",
        "quarantine",
        "delivery",
        "supermarket",
        "pull card",
        "available balance",
        "recount",
        "cutting ticket",
        "issued",
        "requisition",
    ),
    "ie": (
        "cycle time",
        "cycle-time",
        "bottleneck",
        "balance index",
        "units per hour",
        "sam value",
        "sam values",
        "standard minute",
        "line balance",
        "theoretical output",
        "time study",
        "outlier",
        "skill gap",
        "method change",
        "constraint",
        "efficiency",
    ),
    "quality": (
        "defect",
        "defects",
        "inspection",
        "inspected",
        "quality hold",
        "hold released",
        "reject",
        "rework",
        "final inspection",
        "shipment",
        "dhu",
        "critical finding",
        "sample size",
        "corrective action",
        "packing held",
        "eligible for packing",
        "metal detector",
        "needle breakage",
        "root cause",
        "insufficient sample",
    ),
}


class KeywordBaselineClassifier:
    """Deterministic, training-free classifier used as an evaluation baseline."""

    def predict(self, texts: Sequence[str]) -> list[str]:
        return [self._predict_one(text) for text in texts]

    @staticmethod
    def _predict_one(text: str) -> str:
        lowered = text.lower()
        best_label = "unknown"
        best_score = 0
        for label in ("planning", "materials", "ie", "quality"):
            score = sum(1 for keyword in _KEYWORDS[label] if keyword in lowered)
            if score > best_score:
                best_score = score
                best_label = label
        return best_label


class TfidfNoteClassifier:
    """`TfidfVectorizer(1,2-grams) + LogisticRegression`, trained on labelled notes."""

    def __init__(self, vectorizer: TfidfVectorizer, model: LogisticRegression) -> None:
        self._vectorizer = vectorizer
        self._model = model

    @classmethod
    def train(cls, examples: Sequence[tuple[str, str]], *, seed: int = 13) -> TfidfNoteClassifier:
        texts = [text for text, _label in examples]
        labels = [label for _text, label in examples]
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        features = vectorizer.fit_transform(texts)
        model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)
        model.fit(features, labels)
        return cls(vectorizer, model)

    def predict(self, texts: Sequence[str]) -> list[str]:
        features = self._vectorizer.transform(texts)
        return [str(label) for label in self._model.predict(features)]

    def predict_with_margin(self, texts: Sequence[str]) -> list[tuple[str, float]]:
        features = self._vectorizer.transform(texts)
        probabilities = self._model.predict_proba(features)
        classes = self._model.classes_
        results: list[tuple[str, float]] = []
        for row in probabilities:
            best_index = int(row.argmax())
            top_probability = float(row[best_index])
            top_label = str(classes[best_index])
            if top_probability < MARGIN_THRESHOLD:
                results.append(("unknown", top_probability))
            else:
                results.append((top_label, top_probability))
        return results


def _load_examples(path: Path) -> list[tuple[str, str]]:
    """Read `{"text", "label", ...}` JSONL rows via the builtin `open`.

    Deliberately a thin wrapper around `open()` (not e.g. `Path.read_text`)
    so tests can patch `builtins.open` and assert exactly which dataset
    file was read (task-18-brief.md: "training uses only the train file").
    """
    examples: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row: dict[str, Any] = json.loads(line)
            examples.append((row["text"], row["label"]))
    return examples


def training_file_version(path: Path = TRAIN_DATASET_PATH) -> str:
    """First 12 hex characters of the training file's sha256 digest."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:12]


_default_lock = threading.Lock()
_default_classifier: TfidfNoteClassifier | None = None
_default_version: str | None = None


def get_default_classifier() -> TfidfNoteClassifier:
    """The process-wide `TfidfNoteClassifier`, trained once from the train split.

    Training happens lazily on first use and is cached for the life of the
    process; call `reset_default_classifier_cache` (tests only) to force a
    retrain.
    """
    global _default_classifier, _default_version
    with _default_lock:
        if _default_classifier is None:
            examples = _load_examples(TRAIN_DATASET_PATH)
            _default_classifier = TfidfNoteClassifier.train(examples)
            _default_version = training_file_version()
        return _default_classifier


def get_default_classifier_version() -> str:
    """The cached classifier's version string (sha256(train file)[:12])."""
    if _default_version is None:
        get_default_classifier()
    assert _default_version is not None
    return _default_version


def reset_default_classifier_cache() -> None:
    """Test-only: drop the cached classifier so the next call retrains it."""
    global _default_classifier, _default_version
    with _default_lock:
        _default_classifier = None
        _default_version = None
