---
slug: cycle-time-outlier-handling
title: Cycle-Time Outlier Handling
doc_type: IE_STANDARD
scope: org
acl: []
version: 1
---
# Cycle-Time Outlier Handling

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This procedure defines how an unusually high or low observed cycle-time reading is identified and handled before it is allowed to influence a SAM value.

## Identifying an Outlier

An observation is a candidate outlier when it deviates substantially from the median of the other observations for the same operation, line, and style, most often caused by a bundle interruption, a machine stoppage, or an operator new to the operation, rather than the operation's normal variation.

## Flagging

A candidate outlier is flagged in the observation record rather than silently deleted, preserving the raw data for later review. A flagged observation is excluded from the SAM derivation only after a named reviewer approves the exclusion; an unapproved flag still counts toward the study until it is either approved or reversed.

## Approval Authority

Only the IE engineer role may approve excluding an outlier observation from a time study. A supervisor or planner may flag a suspicious reading for review but may not approve its exclusion themselves.

## Pattern Review

If outliers cluster around a specific operator or a specific time of day rather than appearing randomly, the IE engineer investigates the underlying cause (fatigue, a recurring bundle delay, or a training gap) instead of simply excluding the readings and moving on, since a recurring pattern usually points to a fixable line problem.

## Related Procedures and Review

This procedure is applied every time a new time study is conducted under Time Study Procedure and whenever Bottleneck Escalation raises a concern about an operation's recorded cycle time. Exclusion decisions and their stated reasons are reviewed together by the IE engineer and the line supervisor at least monthly, so that outlier handling does not drift into routinely excluding any inconvenient reading; a pattern of excessive exclusions for the same operation is itself treated as a signal worth investigating. The review also checks that flagged-but-not-yet-approved observations are cleared promptly, since a study left with unresolved flags for more than a few days can stall the SAM value it was meant to update.
