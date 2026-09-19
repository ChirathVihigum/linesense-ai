"""HTTP client for the internal dispatch API (backend-contracts.md section 6).

The orchestrator dispatches work by POSTing a :class:`TaskEnvelope` to
``/internal/v1/agent-tasks`` with the service token. Transport errors and 5xx
responses are retryable; a rejected envelope (4xx) is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

import httpx

from app.db.models import AnalysisRun
from app.orchestration.protocol import (
    SCHEMA_VERSION,
    DispatchReceipt,
    InputRef,
    Recipient,
    TaskConstraints,
    TaskEnvelope,
    idempotency_key_for,
)

DISPATCH_PATH = "/internal/v1/agent-tasks"


class DispatchError(Exception):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


def make_envelope(
    run: AnalysisRun,
    *,
    recipient: str,
    task_type: str,
    round: int,  # noqa: A002 - the protocol field is named "round"
    parent_task_id: uuid.UUID | None,
    input_refs: list[InputRef],
    deadline_at: datetime,
) -> TaskEnvelope:
    """Build the envelope for ``run``'s next task (ids and key come from the run)."""
    if run.snapshot_id is None:
        raise ValueError(f"run {run.id} has no snapshot yet")
    return TaskEnvelope(
        schema_version=SCHEMA_VERSION,
        message_id=uuid.uuid4(),
        run_id=run.id,
        parent_task_id=parent_task_id,
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        order_id=run.order_id,
        snapshot_id=run.snapshot_id,
        sender="orchestrator",
        # The model re-validates the recipient literal and its task types.
        recipient=cast("Recipient", recipient),
        task_type=task_type,
        idempotency_key=idempotency_key_for(run.id, recipient, run.snapshot_id, round),
        round=round,
        deadline_at=deadline_at,
        input_refs=input_refs,
        constraints=TaskConstraints(),
        trace_id=run.trace_id,
    )


class AgentDispatchClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            transport=self._transport,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
        )

    async def submit(self, envelope: TaskEnvelope) -> DispatchReceipt:
        """POST ``envelope``; 202 (created) and 200 (duplicate) both return a receipt."""
        try:
            async with self._client() as client:
                response = await client.post(DISPATCH_PATH, json=envelope.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise DispatchError(f"dispatch transport error: {exc!r}", retryable=True) from exc
        if response.status_code in (200, 202):
            return DispatchReceipt.model_validate(response.json())
        raise DispatchError(
            f"dispatch rejected with HTTP {response.status_code}: {self._error_code(response)}",
            retryable=response.status_code >= 500 or response.status_code == 429,
            status_code=response.status_code,
        )

    async def get(self, task_id: uuid.UUID) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(f"{DISPATCH_PATH}/{task_id}")
        except httpx.HTTPError as exc:
            raise DispatchError(f"dispatch transport error: {exc!r}", retryable=True) from exc
        if response.status_code == 200:
            body: dict[str, Any] = response.json()
            return body
        raise DispatchError(
            f"agent-task lookup failed with HTTP {response.status_code}",
            retryable=response.status_code >= 500,
            status_code=response.status_code,
        )

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        """The contract error code of a rejection (never its message/body text)."""
        try:
            payload = response.json()
        except ValueError:
            return "UNKNOWN"
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return str(code) if code else "UNKNOWN"
