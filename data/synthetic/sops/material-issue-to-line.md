---
slug: material-issue-to-line
title: Material Issue to Line
doc_type: SOP
scope: org
acl: []
version: 1
---
# Material Issue to Line

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure governs the physical issue of reserved material from the store to a production line against a cutting ticket or trims requisition, at both KTN and BYG. It applies to fabric, thread, and trims alike once a lot has been marked ACCEPTED and a reservation has been recorded for the destination order.

## Issue Procedure

Material handlers issue only from lots the reservation references, verify the roll or carton identifier against the pick list, and record the actual quantity issued, which may be less than the reserved quantity when a roll is short-measured on the cutting table. Any shortfall discovered at issue time is reported back to the storekeeper the same shift.

## Wastage Allowance

Gross demand for a style already includes an approved wastage fraction applied once, at planning time: gross_demand = planned_units * BOM_quantity_per_unit * (1 + wastage_fraction). Material handlers issuing at the line must not apply an additional informal wastage margin on top of what planning already reserved, since doing so silently starves other orders of stock that was never actually needed.

## Consumption Recording

Once material is issued and used, the corresponding reservation moves to CONSUMED and a stock movement is recorded against the material and factory. Consumption records are never edited after the fact to make a balance reconcile; a discrepancy is instead investigated and recorded as a separate adjustment movement with a stated reason.

## Return of Excess Material

Fabric or trims issued but not consumed by the end of a style's cutting run are returned to the store the same day, counted, and credited back against the order's reservation before the reservation is released. Uncounted returns left on the line overnight are treated as a stock discrepancy, not as automatically available.

## Related Procedures and Review

Material handlers follow this procedure together with Cutting Ticket Procedure for fabric and Supermarket Replenishment for trims, since a single order's material may arrive at the line through both routes. Any wastage variance discovered during reconciliation that repeats across multiple orders for the same style is escalated to the planner as a possible bill-of-materials error rather than treated as an isolated issue event. This procedure is reviewed whenever the Material Reservation Policy changes, since issue depends directly on the reservation lifecycle it defines.
