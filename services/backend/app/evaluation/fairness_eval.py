"""Fairness section (task-19-brief.md requirement 7).

For each of several pairs, the *same* order is run through the four-agent
flow twice, with only its customer changed in between, and the stored
``recommendations.proposal_hash`` values are compared. This -- not two
different orders with matching business content -- is the meaningful
"identical except the customer" comparison: `proposal_hash` is computed
over a proposal that embeds `order_id` (see
``app.orchestration.recommendations.create_recommendation``), so two
different orders can never hash identically no matter how similar their
content is.

**Limitation** (this harness's own statement; the original product spec's
numbered fairness clause was not available in this environment, so this is
written from scratch, deliberately conservative rather than copied):
this only shows that customer identity does not change the *deterministic*
allocation this flow proposes, under the fixture LLM provider. It is not a
fairness audit across any protected characteristic, it says nothing about
a live model (which, unlike the fixture provider, could in principle let a
customer's name or account history leak into its phrasing or tool choices
even when the deterministic assessment is identical), and a 100% pass rate
here is a necessary, not sufficient, condition for calling the system fair.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Customer, Order
from app.evaluation.agent_eval import (
    _build_scenario_order,
    _generate_scenario_values,
    _run_four_agent_flow,
)
from app.settings import Settings

FAIRNESS_LIMITATION_STATEMENT = (
    "This test only shows that the four-agent flow, under the deterministic fixture LLM "
    "provider, proposes byte-identical allocations for the same order regardless of which "
    "customer it belongs to. It is not an audit of fairness across any protected "
    "characteristic, it cannot show whether a live model would let a customer's identity "
    "leak into its phrasing or tool choices, and passing it is necessary but not sufficient "
    "for calling the system fair."
)


async def run_fairness_eval(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    transport: httpx.AsyncBaseTransport,
    *,
    pairs: int,
) -> dict[str, Any]:
    from tests.factories import make_factory, make_org

    async with session_factory() as session:
        organization = await make_org(session, name="Eval Org (fairness pairs)")
        factory = await make_factory(session, organization=organization, code="FAI")
        await session.commit()

    pair_reports: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for pair_index in range(pairs):
        values = _generate_scenario_values(1000 + pair_index)  # disjoint seed space from agent_eval
        try:
            async with session_factory() as session:
                order = await _build_scenario_order(
                    session,
                    organization=organization,
                    factory=factory,
                    values=values,
                    index=pair_index,
                    customer_code=f"FAIR-{pair_index}-A",
                )
            first = await _run_four_agent_flow(
                session_factory,
                settings,
                transport,
                organization=organization,
                factory=factory,
                values=values,
                index=pair_index,
                order=order,
            )
            await _reassign_customer(
                session_factory,
                order_id=order.id,
                organization_id=organization.id,
                customer_code=f"FAIR-{pair_index}-B",
            )
            second = await _run_four_agent_flow(
                session_factory,
                settings,
                transport,
                organization=organization,
                factory=factory,
                values=values,
                index=pair_index,
                order=order,
            )
        except Exception as exc:  # noqa: BLE001 - recorded honestly, never crashes the harness
            errors.append({"pair": pair_index, "error": str(exc)})
            continue

        hash_a = first.get("recommendation_proposal_hash")
        hash_b = second.get("recommendation_proposal_hash")
        identical = hash_a is not None and hash_a == hash_b
        pair_reports.append(
            {
                "pair": pair_index,
                "identical": identical,
                "hash_a": hash_a,
                "hash_b": hash_b,
                "recommendation_created_a": first.get("recommendation_created"),
                "recommendation_created_b": second.get("recommendation_created"),
            }
        )

    passed = sum(1 for r in pair_reports if r["identical"])
    return {
        "pairs_requested": pairs,
        "pairs_completed": len(pair_reports),
        "errors": errors,
        "pairs": pair_reports,
        "pass_rate": (passed / len(pair_reports)) if pair_reports else None,
        "limitation": FAIRNESS_LIMITATION_STATEMENT,
        "not_a_live_llm_evaluation": True,
    }


async def _reassign_customer(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    order_id: Any,
    organization_id: Any,
    customer_code: str,
) -> None:
    async with session_factory() as session:
        customer = Customer(
            organization_id=organization_id, code=customer_code, name="Eval Customer"
        )
        session.add(customer)
        await session.flush()
        order = await session.get(Order, order_id)
        assert order is not None
        order.customer_id = customer.id
        order.version += 1
        await session.commit()
