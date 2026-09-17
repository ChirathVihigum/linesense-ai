---
slug: bottleneck-escalation
title: Bottleneck Escalation
doc_type: SOP
scope: org
acl: []
version: 1
---
# Bottleneck Escalation

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure defines when a line's bottleneck operation is escalated for review and who is responsible for investigating it, following the Line Balancing Method.

## Identifying the Bottleneck

The bottleneck for a line at a point in time is the operation with the largest effective cycle time among the style's operations currently running on that line; theoretical output for the line is bounded by this operation regardless of how fast every other operation runs.

## Escalation Trigger

If the line's model balance index falls below this procedure's internal review threshold of 75% for two consecutive shifts, or if the same operation is repeatedly identified as the bottleneck across three or more style runs, the line supervisor and the IE engineer jointly investigate before the next shift starts.

## Investigation

Investigation covers whether the bottleneck's SAM value still reflects how the operation is performed, whether additional parallel operators are available and skill-matched, and whether a method or attachment change could reduce the operation's cycle time without introducing a new defect risk.

## Resolution and Record

The chosen resolution — added parallel capacity, a method change, a re-study, or acceptance of the current balance — is recorded against the line and style so future bottleneck escalations for the same style can see what was already tried, rather than repeating an investigation with the same conclusion.

## Related Procedures and Review

This procedure is triggered by the calculations defined in Line Balancing Method and, where a SAM value itself is suspect, feeds back into Time Study Procedure for a formal re-study. Escalations and their resolutions are reviewed monthly across all lines at both plants so IE can see whether the same operation recurs as a bottleneck across multiple styles, which would point to a shared root cause such as an underpowered machine type rather than a style-specific issue. Where a shared root cause is confirmed, the fix is scheduled once across every affected line rather than resolved piecemeal each time the same bottleneck resurfaces on a different order.
