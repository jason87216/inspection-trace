"""Normalize units, dates and characters: pure functions."""

import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from math import isfinite
from zoneinfo import ZoneInfo

from openpyxl.utils.datetime import from_excel

from inspection_trace.ingestion.readers import RawCell

TAIPEI = ZoneInfo("Asia/Taipei")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def clean_text(value: RawCell) -> str:
    return "" if value is None else unicodedata.normalize("NFKC", str(value)).strip()


def parse_number(text: str) -> Decimal:
    if not _NUMBER.fullmatch(text) or not isfinite(float(text)):
        raise ValueError("Expected a finite number using a decimal point.")
    return Decimal(text)


def parse_positive_int(text: str) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", text) or not 0 < int(text) <= 2147483647:
        raise ValueError("Expected a positive integer.")
    return int(text)


def parse_roc_day(text: str) -> date:
    if not re.fullmatch(r"[0-9]{7}", text):
        raise ValueError("Expected a 7-digit ROC date.")
    return date(1911 + int(text[:3]), int(text[3:5]), int(text[5:]))


def parse_clock(text: str) -> time:
    if not re.fullmatch(r"[0-9]{6}", text):
        raise ValueError("Expected time in HHMMSS.")
    return time(int(text[:2]), int(text[2:4]), int(text[4:]))


def parse_timestamp(value: RawCell, *, machine: bool) -> datetime:
    """Accept the declared timestamp formats and attach Taipei time to local values."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and not machine:
        if not 1 <= value < 2_958_466:
            raise ValueError("Excel date is out of range.")
        parsed = from_excel(value)
        if not isinstance(parsed, datetime):
            raise ValueError("Expected a date and a time.")
    else:
        text = clean_text(value)
        if machine and re.fullmatch(r"[0-9]{9,11}", text):
            return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=int(text))).astimezone(
                TAIPEI
            )
        roc = re.fullmatch(r"([0-9]{3})/([0-9]{2}/[0-9]{2} .+)", text)
        if roc:
            text = f"{int(roc[1]) + 1911}/{roc[2]}"
        try:
            parsed = datetime.fromisoformat(text) if "T" in text or " " in text else None
        except ValueError:
            parsed = None
        if parsed is None:
            for date_format in (
                "%Y/%m/%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y%m%d %H%M",
                "%Y%m%d%H%M%S",
            ):
                try:
                    parsed = datetime.strptime(text, date_format)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise ValueError("Unrecognized measurement date and time.")
    return parsed.replace(tzinfo=TAIPEI) if parsed.tzinfo is None else parsed.astimezone(TAIPEI)


def unit_multiplier(source: str, target: str) -> Decimal:
    unit = source.casefold()
    if unit == target.casefold() or target == "μm" and unit in {"um", "μm", "微米"}:
        return Decimal(1)
    if target == "μm" and unit == "mil":
        return Decimal("25.4")
    raise ValueError("Unrecognized unit for the inspection item.")


def normalize_value(number: Decimal, multiplier: Decimal) -> float:
    with localcontext() as context:
        context.prec = max(28, number.adjusted() + 10)
        result = float((number * multiplier).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    if not isfinite(result):
        raise ValueError("Normalized measurement is out of range.")
    return result
