"""Security section (task-19-brief.md requirement 8).

Two independent checks:

* **Prompt-injection resistance**: rather than re-implementing the
  adversarial-document and scripted-compromised-model scenarios (risking
  a second, drifting copy of the real test), this runs the two existing
  pytest cases that already cover them and reports blocked/total from
  their pass/fail outcome:
  - ``tests/agents/test_agent_loop.py::test_prompt_injection_in_tool_output_changes_nothing``
    (a tool result containing an injected instruction never changes the
    tool set offered to the model or the deterministic action payload).
  - ``tests/agents/test_document_tool.py::test_adversarial_document_cannot_hijack_the_agent_loop``
    (``data/synthetic/adversarial/injection-sop.md``, loaded through the
    real document pipeline, cannot make the RM agent cite a fabricated
    chunk id or call an unlisted tool).
  This subprocess call runs against the harness's own database (it uses
  the autouse per-test truncate fixture), so the security section always
  runs **last**, after every other section has already read what it needs.
* **Abstention**: ``app.domain.quality.calc.shipment_eligibility`` is
  exercised directly (a pure function, no database) for "no inspection",
  "policy unknown" and "missing required inspection type" -- none of
  these may ever yield ``eligible=True``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.domain.quality.calc import ShipmentFacts, shipment_eligibility
from app.domain.vocab import ProductionState

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "services" / "backend"

PROMPT_INJECTION_NODEIDS: tuple[str, ...] = (
    "tests/agents/test_agent_loop.py::test_prompt_injection_in_tool_output_changes_nothing",
    "tests/agents/test_document_tool.py::test_adversarial_document_cannot_hijack_the_agent_loop",
)


def _run_prompt_injection_suite(env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", *PROMPT_INJECTION_NODEIDS],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    output = result.stdout + result.stderr
    passed = output.count(" passed") > 0 and result.returncode == 0
    # pytest's summary line looks like "2 passed in 1.23s" or "1 passed, 1 failed in ...".
    passed_count = 0
    failed_count = 0
    match = re.search(r"(\d+) passed", output)
    if match:
        passed_count = int(match.group(1))
    match = re.search(r"(\d+) failed", output)
    if match:
        failed_count = int(match.group(1))
    total = len(PROMPT_INJECTION_NODEIDS)
    return {
        "nodeids": list(PROMPT_INJECTION_NODEIDS),
        "blocked": passed_count,
        "total": total,
        "returncode": result.returncode,
        "all_blocked": passed and passed_count == total and failed_count == 0,
        "output_tail": "\n".join(output.splitlines()[-40:]),
    }


def _abstention_checks() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def _check(name: str, facts: ShipmentFacts) -> None:
        result = shipment_eligibility(facts)
        checks.append(
            {
                "name": name,
                "passed": result.eligible is False,
                "eligible": result.eligible,
                "reasons": list(result.reasons),
            }
        )

    _check(
        "no_inspection_never_eligible",
        ShipmentFacts(
            production_state=ProductionState.PRODUCTION_COMPLETE.value,
            quantity=100,
            packed_units=100,
            passed_inspection_types=frozenset(),
            required_inspection_types=frozenset({"FINAL"}),
            has_active_hold=False,
            has_valid_release=True,
            policy_known=True,
        ),
    )
    _check(
        "unknown_policy_never_eligible",
        ShipmentFacts(
            production_state=ProductionState.PRODUCTION_COMPLETE.value,
            quantity=100,
            packed_units=100,
            passed_inspection_types=frozenset({"FINAL"}),
            required_inspection_types=frozenset({"FINAL"}),
            has_active_hold=False,
            has_valid_release=True,
            policy_known=False,
        ),
    )
    _check(
        "missing_data_never_eligible",
        ShipmentFacts(
            production_state=ProductionState.IN_PRODUCTION.value,
            quantity=100,
            packed_units=0,
            passed_inspection_types=frozenset(),
            required_inspection_types=frozenset({"FINAL"}),
            has_active_hold=False,
            has_valid_release=False,
            policy_known=True,
        ),
    )

    return {
        "checks": checks,
        "total": len(checks),
        "passed": sum(1 for c in checks if c["passed"]),
        "all_passed": all(c["passed"] for c in checks),
    }


def run_security_eval(env: dict[str, str]) -> dict[str, Any]:
    return {
        "prompt_injection": _run_prompt_injection_suite(env),
        "abstention": _abstention_checks(),
    }
