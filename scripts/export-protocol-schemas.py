#!/usr/bin/env python3
"""Export the agent protocol's JSON schemas and example messages to `contracts/`.

Run from the backend so `app` is importable:

    cd services/backend && uv run python ../../scripts/export-protocol-schemas.py

Output is deterministic (sorted keys, fixed example ids and timestamps), so
re-running it against an unchanged protocol rewrites byte-identical files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "services" / "backend"
CONTRACTS_DIR = REPO_ROOT / "contracts"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.orchestration.protocol import AgentResult, TaskEnvelope  # noqa: E402

RUN_ID = "3f8b1a52-0000-4000-8000-000000000001"
TASK_ID = "3f8b1a52-0000-4000-8000-000000000002"
SNAPSHOT_ID = "3f8b1a52-0000-4000-8000-000000000003"
ORDER_ID = "3f8b1a52-0000-4000-8000-000000000004"
ORG_ID = "3f8b1a52-0000-4000-8000-000000000005"
FACTORY_ID = "3f8b1a52-0000-4000-8000-000000000006"
MESSAGE_ID = "3f8b1a52-0000-4000-8000-000000000007"
BALANCE_ID = "3f8b1a52-0000-4000-8000-000000000008"

EXAMPLE_ENVELOPE = {
    "schema_version": "1.0",
    "message_id": MESSAGE_ID,
    "run_id": RUN_ID,
    "parent_task_id": None,
    "organization_id": ORG_ID,
    "factory_id": FACTORY_ID,
    "order_id": ORDER_ID,
    "snapshot_id": SNAPSHOT_ID,
    "sender": "orchestrator",
    "recipient": "rm",
    "task_type": "assess_material_readiness",
    "idempotency_key": f"{RUN_ID}:rm:{SNAPSHOT_ID}:round-0",
    "round": 0,
    "deadline_at": "2026-09-17T08:32:00Z",
    "input_refs": [
        {"type": "snapshot", "id": SNAPSHOT_ID, "version": None},
        {"type": "order", "id": ORDER_ID, "version": 3},
    ],
    "constraints": {"max_tool_calls": 4, "read_only": True},
    "trace_id": "9d2b0f6c-1111-4000-8000-000000000000",
}

EXAMPLE_RESULT = {
    "schema_version": "1.0",
    "task_id": TASK_ID,
    "agent": "rm",
    "status": "SUCCEEDED",
    "summary": "Material M01 is short by 160 m for the remaining 1000 units.",
    "summary_source": "model",
    "findings": [
        {
            "finding_id": "rm-1",
            "severity": "critical",
            "code": "MATERIAL_SHORTAGE",
            "message": "M01: demand 1260 m exceeds available 1100 m (shortage 160 m).",
            "evidence_ids": ["ev-1", "ev-2"],
            "source": "deterministic",
        }
    ],
    "metrics": [
        {"name": "coverable_units", "value": "873", "unit": "units", "note": None},
        {"name": "shortage:M01", "value": "160", "unit": "m", "note": None},
    ],
    "recommended_actions": [
        {
            "action_id": "rm-replenish-M01",
            "kind": "REPLENISHMENT_SUGGESTION",
            "summary": "Suggest replenishing 160 m of M01 before 2026-09-20.",
            "payload": {
                "material_code": "M01",
                "suggested_quantity": "160",
                "unit": "m",
                "needed_by": "2026-09-20",
                "note": "Suggestion only — no purchase order is created",
            },
            "evidence_ids": ["ev-1"],
            "rank": 0,
            "source": "deterministic",
        }
    ],
    "evidence_refs": [
        {
            "evidence_id": "ev-1",
            "kind": "record",
            "record_type": "material_balance",
            "record_id": BALANCE_ID,
            "record_version": 4,
            "document_id": None,
            "document_version_id": None,
            "chunk_id": None,
            "page_number": None,
            "section": None,
            "description": "M01 balance at KTN: 1500 on hand, 400 reserved (version 4).",
        },
        {
            "evidence_id": "ev-2",
            "kind": "calculation",
            "record_type": None,
            "record_id": None,
            "record_version": None,
            "document_id": None,
            "document_version_id": None,
            "chunk_id": None,
            "page_number": None,
            "section": None,
            "description": "gross_demand(1000, 1.2, 0.05) = 1260 m",
        },
    ],
    "warnings": [],
    "input_versions": {
        "order": {ORDER_ID: 3},
        "material_balances": {BALANCE_ID: 4},
        "capacity_slots": {},
        "quality_policy": {},
    },
    "data_quality": {"complete": True, "missing": [], "notes": []},
    "execution_metadata": {
        "provider": "fixture",
        "model": "fixture-scripted-v1",
        "model_calls": 2,
        "tool_calls": ["get_material_position", "get_bom_demand"],
        "input_tokens": 1200,
        "output_tokens": 180,
        "prompt_version": "rm-v1",
        "degraded": False,
        "degraded_reason": None,
        "started_at": "2026-09-17T08:30:00Z",
        "completed_at": "2026-09-17T08:30:04Z",
    },
    "error_code": None,
}


def _dump(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render() -> dict[str, str]:
    """Every file this script writes, as ``relative path -> content``."""
    # Validating the examples here keeps them in step with the models: a
    # protocol change that the examples no longer satisfy fails the export.
    TaskEnvelope.model_validate(EXAMPLE_ENVELOPE)
    AgentResult.model_validate(EXAMPLE_RESULT)
    return {
        "agent-task-envelope.schema.json": _dump(TaskEnvelope.model_json_schema()),
        "agent-result.schema.json": _dump(AgentResult.model_json_schema()),
        "examples/task-envelope.json": _dump(EXAMPLE_ENVELOPE),
        "examples/agent-result.json": _dump(EXAMPLE_RESULT),
    }


def main() -> int:
    for relative_path, content in render().items():
        target = CONTRACTS_DIR / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
