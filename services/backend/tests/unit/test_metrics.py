"""Hand-computed examples for the evaluation harness's pure metric helpers
(task-19-brief.md)."""

from __future__ import annotations

import pytest

from app.evaluation.metrics import (
    accuracy,
    confusion_matrix,
    macro_f1,
    micro_prf,
    mrr,
    per_label_prf,
    recall_at_k,
)


def test_recall_at_k_two_of_three_queries_hit() -> None:
    retrieved = [["a", "b"], ["c", "d"], ["x", "y"]]
    relevant = [{"a"}, {"z"}, {"y"}]

    assert recall_at_k(retrieved, relevant, k=2) == pytest.approx(2 / 3)


def test_recall_at_k_only_counts_within_the_window() -> None:
    retrieved = [["a", "b", "c"]]
    relevant = [{"c"}]

    assert recall_at_k(retrieved, relevant, k=2) == 0.0
    assert recall_at_k(retrieved, relevant, k=3) == 1.0


def test_recall_at_k_empty_relevant_set_is_never_a_hit() -> None:
    assert recall_at_k([["a"]], [set()], k=5) == 0.0


def test_mrr_one_half() -> None:
    retrieved = [["a", "b", "c"], ["x", "y"]]
    relevant = [{"a"}, {"z"}]

    assert mrr(retrieved, relevant) == pytest.approx(0.5)


def test_mrr_uses_first_hit_rank() -> None:
    retrieved = [["a", "b", "c"]]
    relevant = [{"c"}]

    assert mrr(retrieved, relevant) == pytest.approx(1 / 3)


def test_mrr_respects_k_cutoff() -> None:
    retrieved = [["a", "b", "c"]]
    relevant = [{"c"}]

    assert mrr(retrieved, relevant, k=2) == 0.0


def test_micro_prf_toy_set() -> None:
    true_sets = [{("A", 0, 1), ("B", 2, 3)}, {("A", 5, 6)}]
    pred_sets = [{("A", 0, 1)}, {("A", 5, 6), ("C", 7, 8)}]

    precision, recall, f1 = micro_prf(true_sets, pred_sets)

    # tp=2 (A@0-1, A@5-6), fp=1 (C@7-8), fn=1 (B@2-3)
    assert precision == pytest.approx(2 / 3)
    assert recall == pytest.approx(2 / 3)
    assert f1 == pytest.approx(2 / 3)


def test_micro_prf_perfect_match() -> None:
    true_sets = [{("A", 0, 1)}]
    pred_sets = [{("A", 0, 1)}]

    assert micro_prf(true_sets, pred_sets) == (1.0, 1.0, 1.0)


def test_micro_prf_no_predictions_and_no_truth_is_zero() -> None:
    assert micro_prf([set()], [set()]) == (0.0, 0.0, 0.0)


def test_per_label_prf_keeps_examples_distinct() -> None:
    # Two different notes each contain an identical-looking entity span;
    # they must not collapse into a single true positive.
    true_sets = [{("ORDER", 0, 5)}, {("ORDER", 0, 5)}]
    pred_sets = [{("ORDER", 0, 5)}, set()]

    result = per_label_prf(true_sets, pred_sets)

    assert result["ORDER"]["support"] == 2
    assert result["ORDER"]["precision"] == pytest.approx(1.0)
    assert result["ORDER"]["recall"] == pytest.approx(0.5)


def test_per_label_prf_separates_labels() -> None:
    true_sets = [{("A", 0, 1), ("B", 2, 3)}]
    pred_sets = [{("A", 0, 1)}]

    result = per_label_prf(true_sets, pred_sets, labels=["A", "B"])

    assert result["A"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 1}
    assert result["B"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 1}


def test_macro_f1_hand_computed() -> None:
    y_true = ["a", "a", "b", "b"]
    y_pred = ["a", "b", "b", "b"]

    # label a: tp=1 fp=0 fn=1 -> P=1.0 R=0.5 F1=2/3
    # label b: tp=2 fp=1 fn=0 -> P=2/3 R=1.0 F1=0.8
    expected = ((2 / 3) + 0.8) / 2
    assert macro_f1(y_true, y_pred, labels=["a", "b"]) == pytest.approx(expected)


def test_macro_f1_perfect_predictions() -> None:
    y_true = ["a", "b", "c"]
    assert macro_f1(y_true, list(y_true)) == pytest.approx(1.0)


def test_confusion_matrix_ordering_and_counts() -> None:
    y_true = ["a", "b", "a"]
    y_pred = ["a", "a", "b"]
    labels = ["b", "a"]  # deliberately reordered

    matrix = confusion_matrix(y_true, y_pred, labels)

    assert list(matrix.keys()) == ["b", "a"]
    assert list(matrix["b"].keys()) == ["b", "a"]
    assert matrix == {"b": {"b": 0, "a": 1}, "a": {"b": 1, "a": 1}}


def test_accuracy_hand_computed() -> None:
    assert accuracy(["a", "b", "c"], ["a", "b", "x"]) == pytest.approx(2 / 3)


def test_accuracy_empty_is_zero() -> None:
    assert accuracy([], []) == 0.0


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="same length"):
        recall_at_k([["a"]], [], k=1)
    with pytest.raises(ValueError, match="same length"):
        mrr([["a"]], [], k=1)
    with pytest.raises(ValueError, match="same length"):
        micro_prf([set()], [])
    with pytest.raises(ValueError, match="same length"):
        macro_f1(["a"], [])
    with pytest.raises(ValueError, match="same length"):
        confusion_matrix(["a"], [], labels=["a"])
    with pytest.raises(ValueError, match="same length"):
        accuracy(["a"], [])
