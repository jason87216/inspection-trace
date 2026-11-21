"""Measure the wait between two process stations."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Literal

type QTimeStatus = Literal["within_limit", "over_limit", "incomplete", "invalid_order"]


@dataclass(frozen=True)
class QTimeResult:
    status: QTimeStatus
    hours: float | None


def calculate_qtime(
    from_end: datetime | None, to_start: datetime | None, limit_hours: float
) -> QTimeResult:
    """Compare elapsed hours between two timezone-aware events to a positive limit.

    A wait equal to the limit is within limit. Missing events are incomplete.
    When the downstream start precedes the upstream end, return invalid_order.
    Both incomplete and invalid_order have no elapsed hours.
    """
    if not isfinite(limit_hours) or limit_hours <= 0:
        raise ValueError("The Q-time limit must be positive and finite.")
    if from_end is None or to_start is None:
        return QTimeResult("incomplete", None)
    if from_end.utcoffset() is None or to_start.utcoffset() is None:
        raise ValueError("Q-time events must include a timezone.")

    elapsed = to_start.astimezone(UTC) - from_end.astimezone(UTC)
    hours = elapsed.total_seconds() / 3600
    if hours < 0:
        return QTimeResult("invalid_order", None)
    return QTimeResult("over_limit" if hours > limit_hours else "within_limit", hours)
