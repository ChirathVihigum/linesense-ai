---
slug: reorder-point-policy
title: Reorder Point Policy for Raw Materials
doc_type: SOP
scope: org
acl: []
version: 1
---
# Reorder Point Policy for Raw Materials

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure defines how a material's reorder point is calculated and how the store responds when a material's projected balance falls below it. It applies to every material code, from bulk fabrics such as M01 Cotton Pique Fabric to small parts such as M19 Snap Fastener, at both KTN and BYG.

## Reorder Point Formula

The reorder point for a material equals its expected daily consumption multiplied by its lead time in days, plus its configured safety stock: reorder_point = expected_daily_consumption * lead_time_days + safety_stock. Expected daily consumption is derived from recent confirmed order demand for styles that use the material, not from a single day's outlier usage.

## Zero or Unknown Consumption

When a material has no recent consumption history, expected daily consumption is reported as unknown rather than computed as zero, and the reorder point is likewise reported as unknown with an explanation. A material must never appear to have an artificially low reorder point simply because it has not yet been consumed under the current season's style mix.

## Response to a Breach

When a material's projected balance is forecast to fall below its reorder point before the next expected receipt, the storekeeper raises a purchase requisition sized to restore coverage through at least the material's lead time plus safety stock, rounded up to the material's pack size where one is defined.

## Review Cadence

Lead time and safety stock values are reviewed quarterly by the storekeeper together with the planner, and immediately after any supplier lead-time change is confirmed in writing. Safety stock is never reduced solely to make a shortage disappear from a report; any reduction must be justified by a genuine change in supply reliability.

## Related Procedures and Review

Reorder point calculations depend on consumption history that is only reliable once a style has been running for a full production cycle; new styles use a provisional reorder point based on the bill of materials and an estimated ramp-up rate until at least two weeks of real consumption data exist. This procedure is reviewed alongside Material Reservation Policy, since a material's available balance (after reservations) is what a reorder-point breach is actually measured against, not its raw on-hand quantity. The storekeeper keeps a running log of every requisition raised under this policy so lead-time assumptions can be checked against actual supplier performance at the quarterly review.
