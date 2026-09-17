---
slug: material-reservation-policy
title: Material Reservation Policy
doc_type: SOP
scope: org
acl: []
version: 1
---
# Material Reservation Policy

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

A reservation is a claim against a material's accepted on-hand balance made on behalf of a specific order, reducing what is available to other orders without yet reducing the underlying ledger balance. This procedure explains when reservations are created, held, released, and consumed, across both plants.

## Available Balance

A material's available balance for new planning is its accepted on-hand quantity minus its active reservations: available_now = accepted_on_hand - active_reservations. Active reservations are already excluded from this figure, so a new demand calculation must never subtract them a second time when checking coverage for another order.

## Reservation Lifecycle

A reservation begins ACTIVE when an order is planned against a material, moves to CONSUMED when the material is physically issued to the line against that order, and moves to RELEASED if the order is cancelled or the reservation is confirmed no longer needed, for example after a bill-of-materials change. A released reservation immediately returns its quantity to the material's available balance.

## Concurrency Rule

When two requests attempt to reserve material from the same balance at the same time, the system processes them in a strict, deterministic order (by sorted material row identifier) so that at most one of two competing reservations for the full remaining balance can succeed; the second request must see the already-reduced balance and receive a shortage result rather than oversubscribe the material.

## Storekeeper Responsibilities

Storekeepers must never issue material against an order's reservation from a different lot than the one recorded without first confirming the substitute lot is ACCEPTED and of an equivalent specification; substituting fabric shade or trim colour without approval can create a defect the inspection procedures were designed to catch earlier in the process.

## Related Procedures and Review

This policy is the foundation for both Reorder Point Policy, which reasons about a material's available balance, and Material Issue to Line, which consumes a reservation once material physically leaves the store. Storekeepers are trained specifically on the concurrency rule in this document, since misunderstanding it is the most common cause of an apparent stock discrepancy reported by two lines drawing from the same material on the same day. The reservation lifecycle states — ACTIVE, RELEASED, and CONSUMED — are reviewed with new planners during onboarding, using worked examples from both the Katunayake and Biyagama plants.
