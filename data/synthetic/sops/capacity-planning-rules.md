---
slug: capacity-planning-rules
title: Capacity Planning Rules
doc_type: IE_STANDARD
scope: org
acl: []
version: 1
---
# Capacity Planning Rules

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This standard defines how order demand is converted into standard minutes and compared against line capacity for planning purposes, applicable at both KTN and BYG.

## Demand in Standard Minutes

Required standard minutes for an order equal its remaining units multiplied by the style's SAM in minutes per unit: required_standard_minutes = remaining_units * SAM_minutes_per_unit. Standard minutes, not wall-clock time, are the common unit used to compare demand against capacity across different lines and operations.

## Available Capacity

A shift slot's available capacity in standard minutes equals its available operator minutes multiplied by its planned efficiency fraction: available_standard_minutes = available_operator_minutes * planned_efficiency. Planned efficiency is applied exactly once, at this step; it is never applied again later in an IE cycle-time calculation.

## Allocation Policy

Orders are allocated to shift slots using deterministic earliest-due-date scheduling, with ties broken by order reference. An order's units that cannot fit into any slot with remaining capacity, or that are blocked by a line's skill or material readiness, are reported as unscheduled with an explicit reason rather than silently dropped.

## Utilization Reporting

Utilization for a slot is its allocated standard minutes divided by its available standard minutes. A slot with zero available standard minutes is reported as an unknown utilization with an explanation, never as a computed zero or an error that hides the underlying capacity problem.

## Related Procedures and Review

This standard underlies both Line Loading Procedure and Due-Date Risk Escalation, which apply its standard-minutes arithmetic at the level of a specific plant and a specific order respectively. Planned efficiency assumptions for each line are reviewed quarterly by the IE engineer against recent observed throughput, following the Line Balancing Method, and any change is applied prospectively to future shift slots rather than retroactively to slots already used for completed planning decisions. Where a planner believes a specific slot's efficiency assumption is now materially wrong, the correct channel is a request to IE for review under this standard, not an ad hoc adjustment made only to that slot to force a schedule to appear feasible.
