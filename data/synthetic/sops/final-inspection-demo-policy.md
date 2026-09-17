---
slug: final-inspection-demo-policy
title: Final Inspection Demo Policy
doc_type: QUALITY_POLICY
scope: org
acl: []
version: 1
---
# Final Inspection Demo Policy

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This is a demonstration quality policy, not a certified AQL/ISO-compliant standard; it approximates the shape of an AQL-style sampling policy for teaching purposes and must never be presented as a certified factory standard. It governs the FINAL inspection required before an order becomes eligible for packing and shipment.

## Sample Size

The approved demo policy requires a sample size of 80 units per FINAL inspection, drawn across the order's production run rather than only from the last carton produced, so the sample reflects the whole run rather than only its most recent output.

## Acceptance Numbers

The lot is disposed as PASS only if the number of defective units found in the sample of 80 does not exceed 5, and no critical defect (such as metal contamination, DEF-MC) is found at all — the maximum critical defects allowed under this demo policy is 0. A sample smaller than 80 units yields an INSUFFICIENT_SAMPLE result, never a PASS.

## Required Inspection Type

FINAL inspection is required for every order before it may be marked shipment ready; INLINE inspection results, however thorough, do not substitute for a completed FINAL inspection against this policy.

## Policy Versioning

This demo policy is versioned; an order's inspection is always evaluated against the specific approved policy version active at the time of inspection, not against whatever version happens to be active when a report is generated later. Every reported disposition is shown next to the policy version it was decided against.

## Related Procedures and Review

This policy's acceptance numbers are the basis for Critical Defect Response and Quality Hold and Release, both of which reference this document's sample size and defect thresholds directly rather than restating their own. Because this is explicitly a demo policy rather than a certified AQL/ISO standard, it is reviewed by the quality manager before any use beyond training or demonstration, and any real deployment would require replacing it with a policy validated by a qualified quality assurance professional against the customer's actual contractual requirements.
