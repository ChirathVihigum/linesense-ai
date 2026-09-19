"""The agent task protocol: envelope invariants and a stable schema export."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.orchestration.protocol import (
    ALLOWED_TASK_TYPES,
    RETRYABLE,
    AgentErrorCode,
    TaskEnvelope,
    idempotency_key_for,
)

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "export-protocol-schemas.py"


def _envelope_payload(**overrides: Any) -> dict[str, Any]:
    run_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "run_id": str(run_id),
        "parent_task_id": None,
        "organization_id": str(uuid.uuid4()),
        "factory_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "snapshot_id": str(snapshot_id),
        "sender": "orchestrator",
        "recipient": "rm",
        "task_type": "assess_material_readiness",
        "idempotency_key": idempotency_key_for(run_id, "rm", snapshot_id, 0),
        "round": 0,
        "deadline_at": (datetime.now(tz=UTC) + timedelta(seconds=120)).isoformat(),
        "input_refs": [{"type": "snapshot", "id": str(snapshot_id)}],
        "constraints": {"max_tool_calls": 4, "read_only": True},
        "trace_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return payload


def _load_export_module() -> Any:
    spec = importlib.util.spec_from_file_location("export_protocol_schemas", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_envelope_round_trips() -> None:
    envelope = TaskEnvelope.model_validate(_envelope_payload())
    assert envelope.constraints.max_tool_calls == 4
    assert envelope.constraints.read_only is True
    assert TaskEnvelope.model_validate(envelope.model_dump(mode="json")) == envelope


def test_read_only_false_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(
            _envelope_payload(constraints={"max_tool_calls": 4, "read_only": False})
        )


def test_more_than_four_tool_calls_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(
            _envelope_payload(constraints={"max_tool_calls": 5, "read_only": True})
        )


def test_unknown_recipient_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(
            _envelope_payload(recipient="finance", task_type="assess_material_readiness")
        )


def test_task_type_must_belong_to_recipient() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(_envelope_payload(task_type="propose_allocation"))
    assert "propose_allocation" in ALLOWED_TASK_TYPES["planning"]


def test_round_is_bounded_and_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(_envelope_payload(round=2))
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(_envelope_payload(tool_allowlist=["shell"]))


def test_only_provider_unavailable_is_retryable() -> None:
    assert RETRYABLE[AgentErrorCode.PROVIDER_UNAVAILABLE] is True
    assert not any(
        retryable
        for code, retryable in RETRYABLE.items()
        if code is not AgentErrorCode.PROVIDER_UNAVAILABLE
    )


def test_schema_export_is_deterministic_and_complete() -> None:
    module = _load_export_module()
    first = module.render()
    second = module.render()
    assert first == second
    assert set(first) == {
        "agent-task-envelope.schema.json",
        "agent-result.schema.json",
        "examples/task-envelope.json",
        "examples/agent-result.json",
    }
    assert '"schema_version"' in first["agent-task-envelope.schema.json"]
    assert '"execution_metadata"' in first["agent-result.schema.json"]
