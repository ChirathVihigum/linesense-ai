"""Grounded, evidence-backed order status summaries (task-18-brief.md
requirement 4).

`grounded_summary` is a pure deterministic template over the `OrderReport`
shape (task-15-brief.md requirement 4): every sentence it produces carries
the evidence ids that back it, drawn only from the report's own `evidence`
list, so it can never cite something outside the record. `model_summary` is
the optional `?mode=model` path: it asks the LLM boundary for the same
shape (sentences + evidence ids) once, then validates the response against
the same report before trusting it — an invalid or shipment-contradicting
response is discarded in favour of the deterministic summary, never shown
to a user unvalidated.

`OrderReport` is a plain `dict[str, Any]` here (the shape Task 15 stores as
the `run.report` event payload and returns as `RunDetail.report` / a future
`OrderDetail.latest_report` — see `app/api/runs.py`), not a class imported
from `app.orchestration` (Task 15 is developed concurrently): this module
depends only on the documented JSON shape, never on Task 15's internal
types.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.client import LLMClient, LLMDisabledError, LLMError

# Labelling conventions (backend-contracts.md section 8 / task-12-brief.md):
# a fixture or disabled provider must never be presented as a live model.
FIXTURE_LABEL = "Test fixture — not a live AI model"
DISABLED_LABEL = "AI disabled — deterministic results only"
UNAVAILABLE_LABEL = "AI summary unavailable — showing calculated summary"
REJECTED_LABEL = "AI summary rejected — showing calculated summary"

#: A model sentence claiming the order can ship while the deterministic
#: `shipment.eligible` fact says otherwise is rejected outright
#: (task-18-brief.md requirement 4).
_SHIPMENT_CLAIM_RE = re.compile(r"ready to ship|shipment[- ]ready|can ship", re.IGNORECASE)

#: Returns `True` iff the caller may still generate a model summary (the
#: 24h/order cap has not been reached). Checking *and* recording the
#: resulting `summary.model_generated` audit event are both the API
#: route's job (`app/api/summaries.py`) — this module has no DB access.
BudgetReserver = Callable[[], Awaitable[bool]]

#: The documented `OrderReport` JSON shape (task-15-brief.md requirement 4):
#: `{"order", "states", "shipment", "blockers", "agent_summaries",
#: "recommendation", "evidence", "degraded", "degraded_reasons",
#: "generated_at"}`. Plain alias for readability; not a runtime type.
OrderReport = dict[str, Any]


@dataclass(frozen=True)
class SummarySentence:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroundedSummary:
    summary_source: str  # "deterministic" | "model"
    sentences: tuple[SummarySentence, ...]
    label: str | None


def _evidence_ids(report: OrderReport) -> set[str]:
    return {
        str(entry["evidence_id"])
        for entry in report.get("evidence", []) or []
        if isinstance(entry, dict) and "evidence_id" in entry
    }


def grounded_summary(report: OrderReport) -> GroundedSummary:
    """Deterministic template: states, shipment eligibility, each blocker,
    and the recommendation status — every sentence's evidence ids are drawn
    only from the report's own blockers/evidence, so they always exist.
    """
    blockers = report.get("blockers", []) or []
    blocker_evidence = tuple(
        sorted({str(eid) for blocker in blockers for eid in blocker.get("evidence_ids", []) or []})
    )

    order = report.get("order", {}) or {}
    states = report.get("states", {}) or {}
    ref = order.get("external_ref") or "This order"
    states_text = (
        f"{ref} is {states.get('production', 'UNKNOWN')} in production "
        f"(material state {states.get('material', 'UNKNOWN')}, "
        f"quality state {states.get('quality', 'UNKNOWN')})."
    )

    shipment = report.get("shipment", {}) or {}
    eligible = bool(shipment.get("eligible", False))
    reasons = shipment.get("reasons") or []
    if eligible:
        shipment_text = "Shipment is eligible."
    else:
        reason_text = "; ".join(str(reason) for reason in reasons) or "no reason recorded"
        shipment_text = f"Shipment is not eligible: {reason_text}."

    sentences: list[SummarySentence] = [
        SummarySentence(text=states_text, evidence_ids=blocker_evidence),
        SummarySentence(text=shipment_text, evidence_ids=blocker_evidence),
    ]
    for blocker in blockers:
        text = (
            f"Blocker ({blocker.get('severity', 'info')}, {blocker.get('agent', 'system')}): "
            f"{blocker.get('message', '')}"
        )
        ids = tuple(str(eid) for eid in blocker.get("evidence_ids", []) or [])
        sentences.append(SummarySentence(text=text, evidence_ids=ids))

    recommendation = report.get("recommendation")
    if recommendation is not None:
        rec_text = (
            f"Recommendation ({recommendation.get('kind', 'unknown kind')}) is "
            f"{recommendation.get('status', 'unknown status')}."
        )
    else:
        rec_text = "No recommendation has been proposed for this order yet."
    sentences.append(SummarySentence(text=rec_text, evidence_ids=()))

    return GroundedSummary(summary_source="deterministic", sentences=tuple(sentences), label=None)


_MODEL_SYSTEM_PROMPT = (
    "You summarize a garment production order's status from a JSON report. "
    'Reply with ONLY a JSON object {"sentences": [{"text": str, '
    '"evidence_ids": [str, ...]}, ...]}. Every sentence must cite at least '
    "one evidence id, and every id you cite must be present in the report's "
    "`evidence` list. Never state or imply the order is ready to ship, "
    "shipment-ready, or can ship unless `shipment.eligible` is true in the "
    "report — that fact is calculated, not yours to judge."
)


def _parse_sentences(text: str | None) -> list[Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    sentences = data.get("sentences") if isinstance(data, dict) else data
    return sentences if isinstance(sentences, list) else None


def _validate_sentences(
    raw_sentences: Sequence[Any], report: OrderReport
) -> tuple[SummarySentence, ...] | None:
    if not raw_sentences:
        return None
    valid_ids = _evidence_ids(report)
    eligible = bool((report.get("shipment") or {}).get("eligible", False))
    sentences: list[SummarySentence] = []
    for item in raw_sentences:
        if not isinstance(item, dict):
            return None
        text = str(item.get("text") or "").strip()
        ids = item.get("evidence_ids")
        if not text or not isinstance(ids, list) or not ids:
            return None
        id_strs = [str(entry) for entry in ids]
        if any(entry not in valid_ids for entry in id_strs):
            return None
        if not eligible and _SHIPMENT_CLAIM_RE.search(text):
            return None
        sentences.append(SummarySentence(text=text, evidence_ids=tuple(id_strs)))
    return tuple(sentences)


def _fallback(report: OrderReport, label: str) -> GroundedSummary:
    deterministic = grounded_summary(report)
    return GroundedSummary(
        summary_source="deterministic", sentences=deterministic.sentences, label=label
    )


async def model_summary(
    report: OrderReport,
    llm: LLMClient,
    budget_reserver: BudgetReserver,
) -> GroundedSummary | None:
    """One validated, LLM-drafted summary — or `None` if `budget_reserver`
    refuses (the caller, `app/api/summaries.py`, turns that into 429
    `RATE_LIMITED`). Every other outcome (provider disabled/unavailable,
    an unparsable response, or a response that fails validation) degrades
    to the deterministic summary with a label explaining why, and is never
    returned as `None`.
    """
    if not await budget_reserver():
        return None

    provider_label = FIXTURE_LABEL if llm.provider == "fixture" else None

    try:
        response = await llm.complete(
            system=_MODEL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(report, default=str)}],
            tools=[],
            max_tokens=1024,
            timeout_seconds=30.0,
        )
    except LLMDisabledError:
        return _fallback(report, DISABLED_LABEL)
    except LLMError:
        return _fallback(report, UNAVAILABLE_LABEL)

    raw_sentences = _parse_sentences(response.text)
    if raw_sentences is None:
        return _fallback(report, REJECTED_LABEL)
    sentences = _validate_sentences(raw_sentences, report)
    if sentences is None:
        return _fallback(report, REJECTED_LABEL)
    return GroundedSummary(summary_source="model", sentences=sentences, label=provider_label)
