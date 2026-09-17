---
slug: due-date-risk-escalation
title: Due-Date Risk Escalation
doc_type: SOP
scope: org
acl: []
version: 1
---
# Due-Date Risk Escalation

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure defines when an order's due date is considered at risk and how that risk is escalated, covering both material shortages and capacity shortfalls at KTN and BYG.

## Risk Signals

An order is flagged at risk when its projected completion date, computed from current allocations and material coverage, falls after its due date, or when a required material's projected balance is forecast to run short before the order can be fully cut. Either signal alone is sufficient to raise a risk flag; the two are never required to agree before escalation.

## Escalation Path

A material-driven risk is escalated to the storekeeper and planner first, since a reorder or a reservation adjustment may resolve it without touching the schedule. A capacity-driven risk is escalated to the planner and supervisor, who may reassign the order to a different line or shift if one has spare standard minutes before the due date.

## Recommendation Review

Where the system proposes a recommendation to resolve a risk (a reallocation, a reservation change, or both), a supervisor must review and approve or reject it before it is applied; the person who requested the analysis may never approve their own recommendation.

## Recurring Risk

If the same order is flagged at risk on three or more consecutive days without resolution, the planner must bring the order to the next daily production review rather than continuing to re-escalate it through the same channel, since a recurring unresolved risk usually indicates a root cause the single-order escalation path cannot fix.

## Related Procedures and Review

This procedure draws its capacity signal from Capacity Planning Rules and its material signal from Reorder Point Policy and Material Reservation Policy, and its recommendation review step follows the same self-approval prohibition described in the AI Assistant Usage Policy. Recurring risk patterns identified through the daily production review are logged so that a root cause affecting multiple orders, such as a systematically understated SAM value, can be identified and corrected once rather than repeatedly worked around order by order.
