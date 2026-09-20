"""Pure metric helpers for the evaluation harness (task-19-brief.md).

Every helper here is a small, dependency-free function over plain Python
data (lists, sets, tuples) so it can be unit-tested with hand-computed
examples independent of the database, the retrieval pipeline, the NLP
models, or any LLM. See ``tests/unit/test_metrics.py`` for the worked
examples referenced by the brief (recall 2/3, MRR 1/2, a toy micro-F1 set,
a macro-F1 example, and confusion-matrix ordering).
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from typing import Any

Number = float


def recall_at_k(
    retrieved: Sequence[Sequence[Hashable]],
    relevant: Sequence[Iterable[Hashable]],
    k: int,
) -> float:
    """Mean, over queries, of "did any of the top-``k`` retrieved items
    match a relevant item for that query".

    ``retrieved[i]`` is query ``i``'s ranked list of item ids; ``relevant[i]``
    is the set of item ids that would count as a hit for that query. A query
    with an empty relevant set never counts as a hit.
    """
    if len(retrieved) != len(relevant):
        raise ValueError("retrieved and relevant must have the same length")
    if not retrieved:
        return 0.0
    hits = 0
    for items, targets in zip(retrieved, relevant, strict=True):
        target_set = set(targets)
        if target_set and any(item in target_set for item in items[:k]):
            hits += 1
    return hits / len(retrieved)


def mrr(
    retrieved: Sequence[Sequence[Hashable]],
    relevant: Sequence[Iterable[Hashable]],
    k: int | None = None,
) -> float:
    """Mean reciprocal rank of the first relevant item within the top-``k``
    (the whole ranked list when ``k`` is ``None``); a query with no hit in
    that window contributes 0.
    """
    if len(retrieved) != len(relevant):
        raise ValueError("retrieved and relevant must have the same length")
    if not retrieved:
        return 0.0
    total = 0.0
    for items, targets in zip(retrieved, relevant, strict=True):
        target_set = set(targets)
        window = items if k is None else items[:k]
        for rank, item in enumerate(window, start=1):
            if item in target_set:
                total += 1.0 / rank
                break
    return total / len(retrieved)


def micro_prf(
    true_sets: Sequence[Iterable[Hashable]], pred_sets: Sequence[Iterable[Hashable]]
) -> tuple[float, float, float]:
    """Micro-averaged precision/recall/F1 over paired true/predicted item
    sets, one pair per example, by exact item match (e.g. an entity's
    ``(label, start, end)`` tuple, or a document's set of relevant chunks).

    Micro-averaging pools true/false positives/negatives across every
    example before dividing, so a class or example with many items is not
    diluted the way a per-example (macro) average would dilute it.
    """
    if len(true_sets) != len(pred_sets):
        raise ValueError("true_sets and pred_sets must have the same length")
    true_positive = false_positive = false_negative = 0
    for true_items, pred_items in zip(true_sets, pred_sets, strict=True):
        true_set = set(true_items)
        pred_set = set(pred_items)
        true_positive += len(true_set & pred_set)
        false_positive += len(pred_set - true_set)
        false_negative += len(true_set - pred_set)
    return _prf(true_positive, false_positive, false_negative)


def per_label_prf(
    true_sets: Sequence[Iterable[tuple[Any, ...]]],
    pred_sets: Sequence[Iterable[tuple[Any, ...]]],
    labels: Iterable[Any] | None = None,
) -> dict[Any, dict[str, float | int]]:
    """Per-label precision/recall/F1/support for item tuples shaped
    ``(label, ...)`` (e.g. entity spans), one true/predicted set per
    example. The example index is folded into the comparison key so two
    different examples that happen to produce an identical tuple are never
    conflated into a single true positive.
    """
    if len(true_sets) != len(pred_sets):
        raise ValueError("true_sets and pred_sets must have the same length")
    all_labels: set[Any] = set(labels) if labels is not None else set()
    true_by_label: dict[Any, set[tuple[int, tuple[Any, ...]]]] = {}
    pred_by_label: dict[Any, set[tuple[int, tuple[Any, ...]]]] = {}
    for index, (true_items, pred_items) in enumerate(zip(true_sets, pred_sets, strict=True)):
        for item in true_items:
            all_labels.add(item[0])
            true_by_label.setdefault(item[0], set()).add((index, item))
        for item in pred_items:
            all_labels.add(item[0])
            pred_by_label.setdefault(item[0], set()).add((index, item))

    result: dict[Any, dict[str, float | int]] = {}
    for label in sorted(all_labels, key=str):
        true_set = true_by_label.get(label, set())
        pred_set = pred_by_label.get(label, set())
        true_positive = len(true_set & pred_set)
        false_positive = len(pred_set - true_set)
        false_negative = len(true_set - pred_set)
        precision, recall, f1 = _prf(true_positive, false_positive, false_negative)
        result[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": len(true_set),
        }
    return result


def macro_f1(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str] | None = None
) -> float:
    """Unweighted mean of each class's F1 (one-vs-rest), for single-label
    classification (one true/predicted label per example).
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    label_list = list(labels) if labels is not None else sorted(set(y_true) | set(y_pred))
    if not label_list:
        return 0.0
    scores = []
    for label in label_list:
        true_positive = sum(
            1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p == label
        )
        false_positive = sum(
            1 for t, p in zip(y_true, y_pred, strict=True) if t != label and p == label
        )
        false_negative = sum(
            1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p != label
        )
        _, _, f1 = _prf(true_positive, false_positive, false_negative)
        scores.append(f1)
    return sum(scores) / len(scores)


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """``{true_label: {predicted_label: count}}``, in exactly ``labels``
    order (both as the outer key order and each row's inner key order) so
    a rendering never has to re-sort it.
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    matrix: dict[str, dict[str, int]] = {
        true_label: dict.fromkeys(labels, 0) for true_label in labels
    }
    for true_label, pred_label in zip(y_true, y_pred, strict=True):
        if true_label in matrix and pred_label in matrix[true_label]:
            matrix[true_label][pred_label] += 1
    return matrix


def accuracy(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    """Fraction of examples where the predicted label matches exactly."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    if not y_true:
        return 0.0
    correct = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p)
    return correct / len(y_true)


def _prf(
    true_positive: int, false_positive: int, false_negative: int
) -> tuple[float, float, float]:
    precision = (
        true_positive / (true_positive + false_positive)
        if (true_positive + false_positive)
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if (true_positive + false_negative)
        else 0.0
    )
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


__all__ = [
    "accuracy",
    "confusion_matrix",
    "macro_f1",
    "micro_prf",
    "mrr",
    "per_label_prf",
    "recall_at_k",
]
