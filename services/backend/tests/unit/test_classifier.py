"""Unit tests for `app.nlp.classifier` (task-18-brief.md requirement 2)."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from app.nlp import classifier as classifier_module
from app.nlp.classifier import (
    KeywordBaselineClassifier,
    TfidfNoteClassifier,
    get_default_classifier,
    get_default_classifier_version,
    reset_default_classifier_cache,
    training_file_version,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_default_classifier_cache()
    yield
    reset_default_classifier_cache()


def test_keyword_baseline_finds_the_class_with_the_most_hits() -> None:
    baseline = KeywordBaselineClassifier()
    texts = [
        "Reorder point review flagged Cotton Pique Fabric; storekeeper raised a requisition.",
        "bartack is the recurring bottleneck; time study scheduled to confirm cycle time.",
        "FINAL inspection passed with no defects; hold released after corrective action.",
        "Line 1 finished changeover early and is ready to start ST-10 ahead of schedule.",
    ]
    assert baseline.predict(texts) == ["materials", "ie", "quality", "planning"]


def test_keyword_baseline_returns_unknown_when_nothing_matches() -> None:
    baseline = KeywordBaselineClassifier()
    assert baseline.predict(["Routine day, no incidents."]) == ["unknown"]


def _load_examples(path: Path) -> list[tuple[str, str]]:
    examples: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            examples.append((row["text"], row["label"]))
    return examples


@pytest.fixture(scope="module")
def train_examples() -> list[tuple[str, str]]:
    return _load_examples(classifier_module.TRAIN_DATASET_PATH)


def test_training_is_deterministic(train_examples: list[tuple[str, str]]) -> None:
    first = TfidfNoteClassifier.train(train_examples)
    second = TfidfNoteClassifier.train(train_examples)
    texts = [text for text, _label in train_examples[:20]]
    assert first.predict(texts) == second.predict(texts)


def test_low_margin_prediction_falls_back_to_unknown(
    train_examples: list[tuple[str, str]],
) -> None:
    model = TfidfNoteClassifier.train(train_examples)
    # Text sharing no vocabulary with any training example: near-uniform
    # class probabilities, so the top probability should sit under 0.45.
    label, margin = model.predict_with_margin(["Xyzzy plugh wobble frobnicate quux."])[0]
    assert label == "unknown"
    assert margin < 0.45


def test_predict_with_margin_keeps_a_confident_prediction(
    train_examples: list[tuple[str, str]],
) -> None:
    model = TfidfNoteClassifier.train(train_examples)
    text, expected_label = next(
        (text, label) for text, label in train_examples if label == "quality"
    )
    label, margin = model.predict_with_margin([text])[0]
    assert label == expected_label
    assert margin >= 0.45


def test_training_only_ever_opens_the_train_file(monkeypatch: pytest.MonkeyPatch) -> None:
    opened_paths: list[str] = []
    real_open = builtins.open

    def _recording_open(file: object, *args: object, **kwargs: object) -> object:
        opened_paths.append(str(file))
        return real_open(file, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _recording_open)

    get_default_classifier()

    test_dataset_path = classifier_module.TRAIN_DATASET_PATH.with_name("notes_test.jsonl")
    assert str(classifier_module.TRAIN_DATASET_PATH) in opened_paths
    assert str(test_dataset_path) not in opened_paths


def test_default_classifier_is_cached_across_calls() -> None:
    first = get_default_classifier()
    second = get_default_classifier()
    assert first is second


def test_default_classifier_version_matches_training_file_hash() -> None:
    get_default_classifier()
    assert get_default_classifier_version() == training_file_version()
    assert len(get_default_classifier_version()) == 12
