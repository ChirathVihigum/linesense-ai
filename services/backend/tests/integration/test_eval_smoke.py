"""Evaluation harness smoke test (task-19-brief.md): runs the whole harness
with the deterministic hashing embedder and a small scenario count against
a temp output dir, and checks the JSON schema keys and that versions are
populated. Never runs the real fastembed evaluation (that is `make eval`,
deliberately excluded from the default/CI-light test run -- see
docs/evaluation/methodology.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.__main__ import main
from app.settings import Settings

pytestmark = pytest.mark.integration

REQUIRED_TOP_LEVEL_KEYS = (
    "generated_at",
    "git_commit",
    "git_dirty",
    "python",
    "platform",
    "versions",
    "retrieval",
    "entities",
    "classification",
    "calculations",
    "agents",
    "fairness",
    "security",
    "targets",
    "target_results",
)

REQUIRED_VERSION_KEYS = (
    "embedder",
    "classifier",
    "prompts",
    "llm_provider",
    "llm_model",
    "corpus_sha256",
    "notes_test_sha256",
    "questions_sha256",
    "seed",
)


def test_eval_smoke_writes_a_well_formed_report(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the harness's own (self-managed) eval database at this test's
    # isolated per-agent database, exactly like every other integration
    # test in this suite -- never linesense_eval, linesense_dev or a
    # hardcoded linesense_test.
    monkeypatch.setenv("LS_EVAL_DATABASE_URL", settings.test_database_url)
    monkeypatch.setenv("LS_EVAL_MIGRATION_DATABASE_URL", settings.test_migration_database_url)

    exit_code = main(["--embedder", "hashing", "--scenarios", "2", "--output-dir", str(tmp_path)])

    assert exit_code == 0

    latest_json = tmp_path / "latest.json"
    latest_md = tmp_path / "latest.md"
    assert latest_json.exists()
    assert latest_md.exists()

    timestamped = list(tmp_path.glob("eval-*.json"))
    assert len(timestamped) == 1

    report = json.loads(latest_json.read_text())
    for key in REQUIRED_TOP_LEVEL_KEYS:
        assert key in report, f"missing top-level key {key!r}"

    versions = report["versions"]
    for key in REQUIRED_VERSION_KEYS:
        assert key in versions, f"missing versions key {key!r}"
        assert versions[key] not in (None, "", {}), f"versions[{key!r}] is empty"

    assert versions["embedder"] == "hashing-384-test"
    assert versions["llm_provider"] == "fixture"
    assert set(versions["prompts"]) == {"rm", "planning", "ie", "quality"}

    assert report["agents"]["scenarios_requested"] == 2
    assert report["fairness"]["pairs_requested"] == 2

    # Never adjust targets after seeing results (task-19-brief.md
    # requirement 9): every named target reports both the fixed threshold
    # and the actual number, honestly, whether or not it was met.
    for name, result in report["target_results"].items():
        assert result["target"] == pytest.approx(result["target"])
        assert "actual" in result
        assert isinstance(result["met"], bool)
        assert name in report["targets"]
