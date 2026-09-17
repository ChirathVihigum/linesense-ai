---
slug: quality-hold-and-release
title: Quality Hold and Release
doc_type: SOP
scope: org
acl: []
version: 1
---
# Quality Hold and Release

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure covers placing an order on quality hold and the conditions under which a hold may be released, applicable at both KTN and BYG.

## Placing a Hold

A quality hold may be created either by a system rule (for example a failed FINAL inspection or a confirmed critical defect) or manually by the quality manager, always with a stated reason. An order with an active hold must not be packed or marked shipment ready while the hold remains active.

## Multiple Holds

An order may accumulate more than one active hold if separate issues are found; all active holds on an order must be individually released before the order can proceed, since resolving one root cause does not automatically clear an unrelated hold.

## Release Authority

Only the quality manager role may release a hold, and only after recording the corrective action taken and, where the hold followed a failed inspection, a fresh passing inspection result against the currently active policy version.

## Release Record

Every release is recorded with the releasing quality manager, the release timestamp, and notes describing the corrective action, forming a permanent record that is never edited after the fact; a mistaken release is corrected by creating a new hold, not by altering the release record.

## Related Procedures and Review

Holds under this procedure are most often created by Critical Defect Response or a failed inspection under Final Inspection Demo Policy, and must be fully cleared before Packing and Shipment Readiness can consider an order eligible. The list of quality managers authorized to release a hold is reviewed whenever the quality team's roster changes, and every release remains permanently attached to the order's record for later customer or audit review. An order carrying more than one active hold is not treated as partially cleared once some holds are released; it remains blocked from packing until every active hold on it has been released. The releasing quality manager confirms this explicitly against the order's current hold list before signing off, rather than relying on memory of which holds were originally raised.
