---
slug: worker-data-privacy
title: Worker Data Privacy
doc_type: OTHER
scope: org
acl: [org_admin, supervisor, ie_engineer]
version: 1
---
# Worker Data Privacy

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure governs how operator-level data is recorded and protected across the system, applicable at both KTN and BYG, and is restricted to the roles most directly responsible for skill and staffing records.

## Pseudonymous Records Only

Operator-level records, including skill levels and cycle-time observations, are recorded against a pseudonymous operator alias only; no worker's name or other directly identifying personal attribute is ever recorded anywhere in the system, including in free-text notes.

## Access Restriction

Operator alias records and the skill matrix are visible only to the org_admin, supervisor, and ie_engineer roles; a planner, storekeeper, or quality manager has no business need to see individual operator-level skill data and is not granted access to it.

## Notes and Free Text

Anyone recording a shift note must describe events by line, order, operation, or material — never by a worker's name — even when the note concerns an individual's performance; performance concerns are raised through the supervisor's normal personnel process, not recorded as searchable free text in the system.

## Retention and Review

Operator alias data is retained only as long as needed for staffing and skill governance purposes and is reviewed periodically by the org_admin role to confirm no personal attribute has been added to a record by mistake.

## Related Procedures and Review

This procedure sets the access and pseudonymity rules that Skill Matrix Governance and Time Study Procedure both depend on, since both record operator-level data. The org_admin role audits a sample of operator alias records and free-text notes quarterly to confirm no personal name or identifying attribute has been introduced, and any violation found is corrected immediately and reported to the organization's data protection contact. New supervisors and IE engineers are briefed on this procedure during onboarding before they are granted access to any skill matrix or cycle-time observation data, and re-briefed whenever the policy is revised. A request for operator-level data from anyone outside the three permitted roles is declined and logged, regardless of how routine or well-intentioned the request appears.
