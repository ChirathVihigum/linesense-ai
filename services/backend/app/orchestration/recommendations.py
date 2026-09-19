"""Turning the agents' selected actions into one reviewable recommendation.

A recommendation is a *proposal*, never an applied change: it records exactly
what would happen, the versions of every input it was derived from (so it can
be rejected as stale at approval time), and the evidence the agents cited.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisRun, Recommendation
from app.domain.clock import utcnow
from app.domain.vocab import GeneratedBy, RecommendationKind, RecommendationStatus
from app.orchestration.protocol import AgentResult, RecommendedAction

RECOMMENDATION_TTL = timedelta(hours=24)
PROPOSING_AGENT = "planning"
_USABLE_STATUSES = ("SUCCEEDED", "DEGRADED")


def proposal_hash(proposal: dict[str, Any]) -> str:
    """``sha256`` of the canonical JSON encoding of ``proposal`` (hex)."""
    encoded = json.dumps(proposal, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _top_action(result: AgentResult | None, kind: str) -> RecommendedAction | None:
    if result is None or result.status not in _USABLE_STATUSES:
        return None
    actions = sorted(
        (action for action in result.recommended_actions if action.kind == kind),
        key=lambda action: action.rank,
    )
    return actions[0] if actions else None


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _cited_evidence(
    result: AgentResult | None, action: RecommendedAction | None
) -> list[dict[str, Any]]:
    """The evidence ``action`` cites, each tagged with the agent that found it."""
    if result is None or action is None:
        return []
    by_id = {ref.evidence_id: ref for ref in result.evidence_refs}
    cited: list[dict[str, Any]] = []
    for evidence_id in action.evidence_ids:
        ref = by_id.get(evidence_id)
        if ref is None:
            continue
        cited.append({"agent": result.agent, **ref.model_dump(mode="json")})
    return cited


def _referenced_versions(
    snapshot_input_versions: dict[str, Any],
    *,
    section: str,
    ids: set[str],
) -> dict[str, Any]:
    known = snapshot_input_versions.get(section)
    if not isinstance(known, dict):
        return {}
    return {key: value for key, value in known.items() if key in ids}


async def create_recommendation(
    session: AsyncSession,
    run: AnalysisRun,
    snapshot_input_versions: dict[str, Any],
    planning_result: AgentResult | None,
    rm_validation_result: AgentResult | None,
) -> Recommendation | None:
    """Build the run's PROPOSED recommendation, or ``None`` when there is nothing
    to propose (no usable plan, or a plan that allocates no units).

    Runs in the caller's transaction and flushes; the caller commits.
    """
    allocation = _top_action(planning_result, "ALLOCATION")
    if allocation is None:
        return None
    allocated = _decimal(allocation.payload.get("allocated_units"))
    if allocated <= 0:
        return None

    reservation = _top_action(rm_validation_result, "RESERVATION")
    allocations = _rows(allocation.payload, "allocations")
    reservations = _rows(reservation.payload, "reservations") if reservation else []

    proposal: dict[str, Any] = {
        "order_id": str(run.order_id),
        "allocations": allocations,
        "reservations": reservations,
        "unscheduled_units": allocation.payload.get("unscheduled_units"),
        "unscheduled_reason": allocation.payload.get("unscheduled_reason"),
        "option_code": allocation.payload.get("option_code"),
    }
    order_versions = snapshot_input_versions.get("order")
    input_versions: dict[str, Any] = {
        "order": dict(order_versions) if isinstance(order_versions, dict) else {},
        "capacity_slots": _referenced_versions(
            snapshot_input_versions,
            section="capacity_slots",
            ids={str(row.get("slot_id")) for row in allocations},
        ),
        "material_balances": _referenced_versions(
            snapshot_input_versions,
            section="material_balances",
            ids={str(row.get("balance_id")) for row in reservations},
        ),
    }

    rationale = planning_result.summary if planning_result is not None else ""
    if reservation is not None and rm_validation_result is not None:
        rationale = f"{rationale} {rm_validation_result.summary}".strip()

    evidence_refs = {
        "items": [
            *_cited_evidence(planning_result, allocation),
            *_cited_evidence(rm_validation_result, reservation),
        ]
    }
    generated_by = (
        GeneratedBy.MODEL.value
        if planning_result is not None and planning_result.summary_source == "model"
        else GeneratedBy.DETERMINISTIC.value
    )
    kind = (
        RecommendationKind.ALLOCATION_AND_RESERVATION.value
        if reservations
        else RecommendationKind.ALLOCATION.value
    )

    recommendation = Recommendation(
        id=uuid.uuid4(),
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        order_id=run.order_id,
        run_id=run.id,
        kind=kind,
        status=RecommendationStatus.PROPOSED.value,
        proposal=proposal,
        proposal_hash=proposal_hash(proposal),
        input_versions=input_versions,
        rationale=rationale,
        evidence_refs=evidence_refs,
        generated_by=generated_by,
        proposed_by_agent=PROPOSING_AGENT,
        proposer_user_id=run.requested_by,
        expires_at=utcnow() + RECOMMENDATION_TTL,
    )
    session.add(recommendation)
    await session.flush()
    return recommendation


__all__ = ["RECOMMENDATION_TTL", "create_recommendation", "proposal_hash"]
