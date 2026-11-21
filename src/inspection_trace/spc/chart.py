"""Individuals and two-point moving range charts with a fixed baseline."""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import isfinite
from statistics import mean
from typing import Literal

type Signal = Literal["I_OUTSIDE_3SIGMA", "I_EIGHT_SAME_SIDE", "MR_OUTSIDE_LIMIT"]


@dataclass(frozen=True)
class Limits:
    lower: float
    center: float
    upper: float


@dataclass(frozen=True)
class ChartPoint:
    moving_range: float | None
    signals: tuple[Signal, ...] = ()


@dataclass(frozen=True)
class ControlChart:
    individual_limits: Limits | None
    range_limits: Limits | None
    points: list[ChartPoint]


def build_chart(values: Sequence[float], baseline_points: int) -> ControlChart:
    """Use the initial points for limits and evaluate only the monitoring points."""
    if baseline_points < 2 or any(not isfinite(value) for value in values):
        raise ValueError("SPC requires finite observations and at least two baseline points.")
    ranges = [abs(after - before) for before, after in pairwise(values)]
    if any(not isfinite(value) for value in ranges):
        raise ValueError("Moving ranges exceed the representable scale.")

    points = [ChartPoint(None), *(ChartPoint(value) for value in ranges)] if values else []
    if len(values) < baseline_points:
        return ControlChart(None, None, points)

    center = mean(values[:baseline_points])
    mean_range = mean(ranges[: baseline_points - 1])
    offset = 3 * (mean_range / 1.128)
    individual = Limits(center - offset, center, center + offset)
    moving = Limits(0, mean_range, 3.267 * mean_range)
    if not all(isfinite(limit) for limit in (individual.lower, individual.upper, moving.upper)):
        raise ValueError("Control limits exceed the representable scale.")

    previous_side = 0
    run_length = 0
    for index in range(baseline_points, len(values)):
        value, moving_range = values[index], ranges[index - 1]
        signals: list[Signal] = []
        if value < individual.lower or value > individual.upper:
            signals.append("I_OUTSIDE_3SIGMA")
        side = int(value > center) - int(value < center)
        if side == 0:
            run_length = 0
        else:
            run_length = run_length + 1 if side == previous_side else 1
        previous_side = side
        if run_length == 8:
            signals.append("I_EIGHT_SAME_SIDE")
        if moving_range > moving.upper:
            signals.append("MR_OUTSIDE_LIMIT")
        points[index] = ChartPoint(moving_range, tuple(signals))
    return ControlChart(individual, moving, points)
