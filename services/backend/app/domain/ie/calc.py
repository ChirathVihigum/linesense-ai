"""Industrial engineering: representative cycle time, line balance,
bottleneck, and throughput.

Pure functions and immutable dataclasses; no database access or I/O. See
`docs/architecture/formulas.md` for the underlying formulas, assumptions,
and the plan's reference fixture. This is the demo's sequential-operation
model, not a universal industrial line-balance KPI.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


class InsufficientSamples(ValueError):
    """Raised when fewer than `min_samples` cycle observations are given."""


@dataclass(frozen=True)
class OperationCycle:
    operation_id: UUID
    operation_code: str
    representative_seconds: Decimal
    parallel_operators: int
    sample_count: int


@dataclass(frozen=True)
class LineBalanceResult:
    effective_cycles: tuple[Decimal, ...]
    bottleneck_index: int
    bottleneck_effective_seconds: Decimal
    units_per_hour: Decimal
    balance_index_percent: Decimal


def representative_cycle_seconds(samples: Sequence[Decimal], min_samples: int = 3) -> Decimal:
    """Median observed cycle time, in seconds.

    Raises `InsufficientSamples` below `min_samples` observations.
    """
    if len(samples) < min_samples:
        raise InsufficientSamples(f"need at least {min_samples} samples, got {len(samples)}")
    result = statistics.median(samples)
    return Decimal(result)


def effective_cycle_seconds(representative_seconds: Decimal, parallel_operators: int) -> Decimal:
    """`representative_seconds / parallel_operators`."""
    return representative_seconds / parallel_operators


def line_balance(effective_cycles: Sequence[Decimal]) -> LineBalanceResult:
    """Bottleneck, throughput, and this model's balance index for a
    sequence of per-operation effective cycle seconds.

    The bottleneck is the maximum effective cycle (the first such index on
    ties). Never mix seconds and minutes here, and never apply an
    efficiency factor (efficiency is a planning concept applied exactly
    once, upstream).
    """
    if not effective_cycles:
        raise ValueError("effective_cycles must not be empty")

    bottleneck_index = 0
    bottleneck = effective_cycles[0]
    for index, cycle in enumerate(effective_cycles):
        if cycle > bottleneck:
            bottleneck = cycle
            bottleneck_index = index

    units_per_hour = Decimal(3600) / bottleneck
    total = sum(effective_cycles, Decimal(0))
    balance_index_percent = total / (Decimal(len(effective_cycles)) * bottleneck) * 100

    return LineBalanceResult(
        effective_cycles=tuple(effective_cycles),
        bottleneck_index=bottleneck_index,
        bottleneck_effective_seconds=bottleneck,
        units_per_hour=units_per_hour,
        balance_index_percent=balance_index_percent,
    )


def sam_capacity_units_per_hour(
    operators: int, sam_minutes_per_unit: Decimal, planned_efficiency: Decimal
) -> Decimal:
    """`operators * 60 * planned_efficiency / sam_minutes_per_unit`."""
    return Decimal(operators) * Decimal(60) * planned_efficiency / sam_minutes_per_unit


def observed_units_per_hour(units_output: int, hours: Decimal) -> Decimal:
    """`units_output / hours`."""
    return Decimal(units_output) / hours
