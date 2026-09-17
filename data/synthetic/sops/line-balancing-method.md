---
slug: line-balancing-method
title: Line Balancing Method
doc_type: IE_STANDARD
scope: org
acl: []
version: 1
---
# Line Balancing Method

> Synthetic demonstration document — not an official factory procedure.

## Purpose and Scope

This standard defines this system's demonstration line-balance model: how effective cycle times, the bottleneck, theoretical throughput, and the balance index are calculated for a sequential-operation line.

## Effective Cycle Time

Each operation's effective cycle time is its representative single-operator time divided by the number of operators working that operation in parallel: effective_cycle_seconds = representative_single_operator_seconds / parallel_operators. The line's bottleneck is the operation with the largest effective cycle time.

## Worked Example

Style ST-03 runs seven operations in sequence: OP-01 shoulder join, OP-02 collar attach, OP-03 placket attach, OP-04 sleeve set, OP-05 side seam, OP-06 bottom hem, and OP-07 pressing, each staffed by a single operator. If the largest effective cycle time among these seven operations is sixty seconds, theoretical output is 3600 / 60 = 60 units per hour, and the balance index is the sum of every operation's effective cycle time divided by seven times sixty, expressed as a percentage.

## Model Assumptions

This balance index assumes steady flow, comparable operators, and no unmodeled machine or material limit; it is always reported as this model's balance index, not as a universal industrial-engineering KPI, since real line balancing also accounts for work-in-process buffers and multi-skill routing that this demonstration model does not.

## Use for Improvement

A low balance index points IE toward operations with spare capacity relative to the bottleneck, which are candidates for absorbing part of the bottleneck's work through method change or operator reassignment; the index itself never triggers an automatic reassignment, which always requires an IE engineer's review.

## Related Procedures and Review

This method underlies both Bottleneck Escalation, which acts on the identified bottleneck operation, and Style Changeover, which uses the same effective-cycle-time concept when assessing whether a line has settled into a new style. The model's assumptions are reviewed whenever a line's actual measured output, recorded under line measurements, diverges materially from its theoretical output for more than a few consecutive shifts, since a persistent gap usually means one of the model's steady-flow assumptions no longer holds for that line.
