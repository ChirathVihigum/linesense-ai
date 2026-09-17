---
slug: sam-definition-and-maintenance
title: SAM Definition and Maintenance
doc_type: IE_STANDARD
scope: org
acl: []
version: 1
---
# SAM Definition and Maintenance

> Synthetic demonstration document — not an official factory procedure.

## Definition

Standard Allowed Minutes (SAM) is the standard time, in minutes, allowed for one operator to complete one unit of a given operation under defined working conditions. Every style operation in the operation catalogue — for example collar attach or side seam — carries its own SAM value; a style's total SAM is the sum of its operations' SAM values in sequence order.

## Source of SAM Values

SAM values are set by the IE engineer from a time study, following the Time Study Procedure, and are demonstration inputs supplied with this synthetic dataset rather than values measured against a real factory's operators. Every SAM value used in planning must trace back to a recorded time study or an explicitly approved estimate, never to an unrecorded verbal figure.

## Maintenance and Versioning

A style's operation sequence and SAM values are versioned; changing a SAM value creates a new version rather than silently overwriting the value orders were already planned against, so historical planning calculations remain reproducible against the SAM values that were actually in effect at the time.

## Use in Planning

SAM converts order quantity into required standard minutes for capacity planning: required_standard_minutes = remaining_units * SAM_minutes_per_unit. SAM is never adjusted informally to make a tight schedule appear to fit; if the schedule does not fit at the approved SAM, the correct response is a capacity or due-date escalation, not a SAM change.

## Review Trigger

A SAM value is reviewed whenever a cycle-time outlier investigation concludes the original time study no longer reflects how the operation is actually performed, for example after a machine or attachment change, following the Cycle-Time Outlier Handling procedure.

## Related Procedures and Review

SAM values feed directly into Capacity Planning Rules and Line Balancing Method, so an error in a single SAM value can distort both a plant's schedule and its reported line balance index. This document is reviewed together with Time Study Procedure and Cycle-Time Outlier Handling whenever a style's operation sequence changes, since adding, removing, or resequencing an operation invalidates the SAM values that were set against the previous sequence. A style's full SAM history is retained even after a value is superseded, so historical planning runs remain explainable.
