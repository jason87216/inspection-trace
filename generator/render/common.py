"""Helpers shared by the source renderers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeVar

from generator.simulate import Record

TW = timezone(timedelta(hours=8))
K = TypeVar("K")


def group_by(records: Iterable[Record], key: Callable[[Record], K]) -> dict[K, list[Record]]:
    groups: dict[K, list[Record]] = defaultdict(list)
    for r in records:
        groups[key(r)].append(r)
    return dict(sorted(groups.items(), key=lambda kv: str(kv[0])))


def shift_day(ts: datetime) -> date:
    """Night shift (20:00-08:00) belongs to the day it started."""
    return (ts - timedelta(hours=8)).date()


def epoch(ts: datetime) -> int:
    return int(ts.replace(tzinfo=TW).timestamp())


def roc_date(ts: datetime | date) -> str:
    return f"{ts.year - 1911}{ts.month:02d}{ts.day:02d}"


def fmt_number(value: Any, decimals: int) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.{decimals}f}"
