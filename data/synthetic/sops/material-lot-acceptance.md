---
slug: material-lot-acceptance
title: Material Lot Acceptance and Status Transitions
doc_type: SOP
scope: org
acl: []
version: 1
---
# Material Lot Acceptance and Status Transitions

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure defines who may move a received material lot between the three lot statuses used throughout the system — QUARANTINE, ACCEPTED, and REJECTED — and what evidence must exist before each transition. It applies to every material lot at both KTN and BYG, covering fabrics, thread, and trims alike, and complements the Fabric Receiving and Inspection procedure, which governs the inspection itself.

## Authoritative Status

A lot's status is never inferred from its movements or from downstream consumption; it is set explicitly by an authorized inspector and remains the single source of truth for whether the lot may be counted in on-hand accepted stock. Only ACCEPTED lots contribute to a material's available balance; QUARANTINE and REJECTED lots are excluded from every planning and reservation calculation.

## Transition Authority

Moving a lot from QUARANTINE to ACCEPTED requires a completed inspection record referencing the applicable inspection procedure and may only be performed by the storekeeper role after the four-point (or trims equivalent) inspection is logged. Moving a lot to REJECTED may be performed by the storekeeper or the quality manager, and always requires a reason code and a note explaining the specific defect pattern observed.

## Reversal and Correction

An ACCEPTED lot may only be reclassified as REJECTED if a defect is discovered after acceptance (for example, a metal contamination finding during downstream cutting). Reclassifying an already-consumed lot does not retroactively change completed movements; it only prevents further issue from that lot and triggers a review of any orders that consumed material from it after the defect's estimated onset.

## Records

Every status change is recorded with the actor, timestamp, and reason, and is available to the quality manager and org_admin roles for audit. Lot status history is never deleted, even after a lot's full quantity has been consumed or written off, so that a later supplier quality review can reconstruct exactly which lots were accepted and why.

## Related Procedures and Review

This procedure is read together with Fabric Receiving and Inspection for fabric lots and with Trims and Accessories Control for trims lots, since both feed the same status model described here. The list of roles permitted to change a lot's status is reviewed whenever a new storekeeper is onboarded, and access is granted only after the storekeeper has completed inspection training. A lot's status history, once written, is never deleted, even for a lot that has been fully consumed, because a later supplier quality review may need to reconstruct exactly which lots were accepted, when, and by whom, across both the Katunayake and Biyagama plants.
