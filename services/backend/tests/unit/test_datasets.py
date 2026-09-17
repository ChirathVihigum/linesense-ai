"""Tests for `scripts/validate_datasets.py` (Task 16).

The validator lives outside the `app` package (at repo root `scripts/`) so
it can be run as a standalone script without importing the whole backend
app; it is loaded here via `importlib` from its file path, per the task
brief.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_datasets.py"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_datasets", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_datasets"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vd() -> ModuleType:
    return _load_validator()


def test_repo_root_resolves_to_project_root(vd: ModuleType) -> None:
    assert (vd.REPO_ROOT / "data" / "synthetic" / "sops").exists()
    assert (vd.REPO_ROOT / "services" / "backend" / "pyproject.toml").exists()


def test_validate_all_reports_zero_problems_on_real_dataset(vd: ModuleType) -> None:
    problems = vd.validate_all()
    assert problems == [], "\n".join(problems)


# ---------------------------------------------------------------------
# Bad-fixture tests: prove the validator actually detects problems.
# ---------------------------------------------------------------------


def test_detects_bad_entity_span(vd: ModuleType) -> None:
    note = {
        "id": "n-bad-001",
        "text": "PO-KTN-0001 is behind schedule on Line 1.",
        "label": "planning",
        "entities": [
            {"label": "ORDER", "text": "PO-KTN-0001", "start": 0, "end": 5},  # wrong end offset
        ],
    }
    problems = vd.check_note_object(note, context="fixture")
    assert any("does not match entity text" in p for p in problems)


def test_detects_unknown_label(vd: ModuleType) -> None:
    note = {
        "id": "n-bad-002",
        "text": "Routine shift handover with nothing to flag.",
        "label": "not-a-real-label",
        "entities": [],
    }
    problems = vd.check_note_object(note, context="fixture")
    assert any("not in" in p and "label" in p for p in problems)


def test_detects_entity_not_in_master_data(vd: ModuleType) -> None:
    text = "PO-KTN-9999 was mentioned but does not exist in master data."
    note = {
        "id": "n-bad-003",
        "text": text,
        "label": "planning",
        "entities": [
            {
                "label": "ORDER",
                "text": "PO-KTN-9999",
                "start": text.index("PO-KTN-9999"),
                "end": text.index("PO-KTN-9999") + len("PO-KTN-9999"),
            }
        ],
    }
    problems = vd.check_note_object(note, context="fixture")
    assert any("does not resolve to known master data" in p for p in problems)


def test_detects_missing_section_reference(vd: ModuleType) -> None:
    sections_by_slug = {"fabric-receiving-inspection": ["Purpose and Scope", "Procedure"]}
    problems = vd.check_relevant_reference(
        "fabric-receiving-inspection", "Nonexistent Section", sections_by_slug, context="fixture"
    )
    assert any("does not exist in document" in p for p in problems)

    problems = vd.check_relevant_reference(
        "not-a-real-slug", "Purpose and Scope", sections_by_slug, context="fixture"
    )
    assert any("does not exist" in p for p in problems)


def test_detects_personal_name(vd: ModuleType) -> None:
    note = {
        "id": "n-bad-004",
        "text": "Kamal reported the machine was down for an hour.",
        "label": "unknown",
        "entities": [],
    }
    problems = vd.check_note_object(note, context="fixture")
    assert any("personal name" in p for p in problems)


def test_jaccard_flags_near_duplicate_notes(vd: ModuleType) -> None:
    train = [
        {"id": "n-train-999", "text": "Line 3 is running the collar attach operation slowly today."}
    ]
    test = [
        {"id": "n-test-999", "text": "Line 3 is running the collar attach operation slowly today!!"}
    ]
    problems = vd.check_train_test_separation(train, test)
    assert any("too similar" in p for p in problems)


def test_label_distribution_flags_missing_class(vd: ModuleType) -> None:
    notes = [{"label": "planning"} for _ in range(24)] + [{"label": "unknown"} for _ in range(1)]
    problems = vd.check_label_distribution(notes, context="fixture")
    assert any("materials" in p for p in problems)
    assert any("unknown" in p for p in problems)
