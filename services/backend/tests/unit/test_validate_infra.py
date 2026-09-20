"""Unit tests for `scripts/validate-infra.py` (Task 26 polish batch).

The script's filename has a hyphen, so it cannot be `import`ed by name; it is
loaded by file path instead, exactly as its own docstring says it can be
("this module can also be imported directly by tests"). These tests only
exercise pure functions (no filesystem/network access beyond `tmp_path`), so
they need no `integration` marker and no database.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "validate-infra.py"


def _load_validate_infra() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_infra", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_infra = _load_validate_infra()


def test_dockerfile_var_default_of_latest_is_flagged(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:${PYTHON_VERSION:-latest}\n")
    problems = validate_infra.check_image_tags_not_latest([dockerfile])
    assert any("pins tag 'latest'" in problem for problem in problems)


def test_dockerfile_var_default_of_pinned_version_is_not_flagged(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:${PYTHON_VERSION:-3.12-slim}\n")
    problems = validate_infra.check_image_tags_not_latest([dockerfile])
    assert problems == []


def test_compose_image_var_default_of_latest_is_flagged() -> None:
    compose = {
        "services": {
            "db": {"image": "postgres:16"},
            "migrate": {"build": "."},
            "api": {"image": "myrepo/api:${TAG:-latest}"},
            "worker": {"build": "."},
            "web": {"build": ".", "ports": ["80:80"]},
            "keycloak": {"profiles": ["dev"], "command": ["start-dev"]},
        },
        "networks": {"default": {}},
    }
    problems = validate_infra.check_compose(compose)
    assert any("service 'api' pins image tag 'latest'" in problem for problem in problems)


def test_compose_image_var_default_of_pinned_version_is_not_flagged() -> None:
    compose = {
        "services": {
            "db": {"image": "postgres:16", "healthcheck": {}},
            "migrate": {"build": "."},
            "api": {"image": "myrepo/api:${TAG:-1.4.2}"},
            "worker": {"build": "."},
            "web": {"build": ".", "ports": ["80:80"]},
            "keycloak": {"profiles": ["dev"], "command": ["start-dev"]},
        },
        "networks": {"default": {}},
    }
    problems = validate_infra.check_compose(compose)
    assert not any("pins image tag 'latest'" in problem for problem in problems)


def _workflow_with_uses(uses: str) -> dict[str, Any]:
    return {
        "on": {"push": {}, "pull_request": {}, "workflow_dispatch": {}},
        "jobs": {
            "backend": {"steps": [{"uses": uses}]},
            "contracts": {"steps": []},
            "web": {"steps": []},
            "security": {"steps": []},
            "eval": {"if": "github.event_name == 'workflow_dispatch'", "steps": []},
        },
    }


def test_forty_hex_sha_pinned_action_is_accepted_even_starting_with_a_letter() -> None:
    # Starts with a letter ('a'), not a digit, so `PINNED_ACTION_RE` alone
    # (which requires `v?\d...`) would reject it as unpinned.
    sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    assert len(sha) == 40
    workflow = _workflow_with_uses(f"actions/checkout@{sha}")
    problems = validate_infra.check_workflow(workflow)
    assert not any("has no pinned ref" in problem for problem in problems)
    assert not any("floating" in problem for problem in problems)


def test_unpinned_branch_ref_is_still_rejected() -> None:
    # `main` is neither a version tag nor a 40-hex SHA, so it still falls
    # into the generic "no pinned ref" problem (this script's existing,
    # unreachable-in-practice `FLOATING_ACTION_REFS` branch is a separate,
    # out-of-scope issue -- not something this polish batch touches).
    workflow = _workflow_with_uses("actions/checkout@main")
    problems = validate_infra.check_workflow(workflow)
    assert any("has no pinned ref" in problem for problem in problems)


def test_unpinned_ref_without_version_or_sha_is_still_rejected() -> None:
    workflow = _workflow_with_uses("actions/checkout@notaversionornsha")
    problems = validate_infra.check_workflow(workflow)
    assert any("has no pinned ref" in problem for problem in problems)
