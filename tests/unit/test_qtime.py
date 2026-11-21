"""Acceptance cases for the P5 waiting-time calculation."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from inspection_trace.trace.qtime import QTimeResult, calculate_qtime

UPSTREAM_END = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("offset_hours", "expected"),
    [
        (8.0, QTimeResult("within_limit", 8.0)),
        (8.25, QTimeResult("over_limit", 8.25)),
        (None, QTimeResult("incomplete", None)),
        (-0.25, QTimeResult("invalid_order", None)),
    ],
)
def test_calculate_qtime_boundary_and_missing_data(
    offset_hours: float | None, expected: QTimeResult
) -> None:
    downstream_start = (
        UPSTREAM_END + timedelta(hours=offset_hours) if offset_hours is not None else None
    )

    assert calculate_qtime(UPSTREAM_END, downstream_start, 8.0) == expected


def test_calculate_qtime_uses_elapsed_time_when_the_clock_repeats() -> None:
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = first.replace(fold=1)

    assert calculate_qtime(first, second, 8.0) == QTimeResult("within_limit", 1.0)
