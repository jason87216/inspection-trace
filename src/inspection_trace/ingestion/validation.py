"""Validate one raw row: pure function, no database or file access."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, DecimalException
from typing import cast

from inspection_trace.ingestion.errors import (
    E_DATE_INVALID,
    E_LOT_FORMAT,
    E_NOT_NUMERIC,
    E_OPERATOR_FORMAT,
    E_OUT_OF_PHYSICAL_RANGE,
    E_REF_NOT_FOUND,
    E_REQUIRED_MISSING,
    E_UNIT_UNKNOWN,
)
from inspection_trace.ingestion.mapping import SRC_LEGACY, SRC_MACHINE, ImportSheet, InputRow
from inspection_trace.ingestion.normalization import (
    TAIPEI,
    clean_text,
    normalize_value,
    parse_clock,
    parse_number,
    parse_positive_int,
    parse_roc_day,
    parse_timestamp,
    unit_multiplier,
)
from inspection_trace.ingestion.readers import RawCell
from inspection_trace.models import InspectionItem, Lot, Machine

type MeasurementKey = tuple[str, str, str, int, int | None]
_LOT = re.compile(r"L[0-9]{6}[A-Z][0-9]{3}(?:-S[0-9])?")
_OPERATOR = re.compile(r"OP-[0-9]{4}")


@dataclass(frozen=True)
class ImportIssue:
    row_no: int | None
    column: str | None
    code: str
    message: str
    raw_value: str | None = None


@dataclass(frozen=True)
class Measurement:
    lot_no: str
    station_code: str
    item_code: str
    panel: int
    point: int | None
    machine_code: str
    measured_at: datetime
    operator: str
    value: float
    source_row_no: int
    value_column: str
    raw_value: str

    @property
    def key(self) -> MeasurementKey:
        return self.lot_no, self.station_code, self.item_code, self.panel, self.point


@dataclass(frozen=True)
class MasterData:
    lots: dict[str, Lot]
    active_parts: set[str]
    active_stations: set[str]
    machines: dict[str, Machine]
    items: dict[str, InspectionItem]
    items_by_name: dict[tuple[str, str], InspectionItem]
    move_operators: dict[tuple[str, str], str]
    operators: set[str]


@dataclass(frozen=True)
class _Row:
    sheet: ImportSheet
    row: InputRow

    def raw(self, field: str) -> RawCell:
        index = self.sheet.positions[field]
        return self.row.cells[index] if index < len(self.row.cells) else None

    def column(self, field: str) -> str:
        return str(self.sheet.header.cells[self.sheet.positions[field]])

    def issue(self, field: str, code: str, message: str) -> ImportIssue:
        value = self.raw(field)
        return ImportIssue(
            self.row.line_no,
            self.column(field),
            code,
            message,
            None if value is None else str(value),
        )


def validate_row(
    sheet: ImportSheet, raw_row: InputRow, master: MasterData
) -> list[Measurement] | ImportIssue:
    """Return normalized measurements or the row's first error in validation order."""
    row = _Row(sheet, raw_row)
    texts = {field: clean_text(row.raw(field)) for field in sheet.positions}
    for field, value in texts.items():
        if not value:
            return row.issue(field, E_REQUIRED_MISSING, "必填欄位空白")

    parsed: dict[str, object] = {}
    for field, value in texts.items():
        if field == "lot_no":
            value = "".join(value.split())
            if not _LOT.fullmatch(value):
                return row.issue(field, E_LOT_FORMAT, "批號格式不符")
            texts[field] = value
        elif field in {"panel", "point"} or field in sheet.points:
            try:
                parsed[field] = (
                    parse_positive_int(value)
                    if field in {"panel", "point"}
                    else parse_number(value)
                )
            except (ValueError, DecimalException):
                return row.issue(field, E_NOT_NUMERIC, "數字格式錯誤")
        elif field in {"date", "time"}:
            try:
                if sheet.source_type == SRC_LEGACY:
                    parsed[field] = parse_roc_day(value) if field == "date" else parse_clock(value)
                else:
                    parsed[field] = parse_timestamp(
                        row.raw(field), machine=sheet.source_type == SRC_MACHINE
                    )
            except (ValueError, OverflowError):
                return row.issue(field, E_DATE_INVALID, "日期或時間格式錯誤")
        elif field == "operator" and not _OPERATOR.fullmatch(value):
            return row.issue(field, E_OPERATOR_FORMAT, "操作員代碼應為 OP- 加四位數字")

    measured_at = (
        datetime.combine(cast(date, parsed["date"]), cast(time, parsed["time"]), tzinfo=TAIPEI)
        if sheet.source_type == SRC_LEGACY
        else cast(datetime, parsed["time"])
    )
    lot_no = texts["lot_no"]
    lot = master.lots.get(lot_no)
    if lot is None or lot.lot_type != "production" or lot.part_no not in master.active_parts:
        return row.issue("lot_no", E_REF_NOT_FOUND, "批號不存在或料號停用")

    machine_code = texts["machine_code"]
    machine = master.machines.get(machine_code)
    if (
        machine is None
        or machine.station_code not in master.active_stations
        or measured_at < machine.valid_from
        or (machine.valid_to is not None and measured_at >= machine.valid_to)
    ):
        return row.issue("machine_code", E_REF_NOT_FOUND, "機台不存在、站點不符或量測時未啟用")

    station = machine.station_code
    item_name = texts["item_code"]
    item = master.items.get(item_name) or master.items_by_name.get((station, item_name))
    if item is None or not item.active or item.station_code != station:
        return row.issue("item_code", E_REF_NOT_FOUND, "檢驗項目不存在、已停用或站點不符")
    try:
        multiplier = unit_multiplier(texts.get("unit", item.unit), item.unit)
    except ValueError:
        return row.issue("unit", E_UNIT_UNKNOWN, "單位與檢驗項目不相容")

    station_operator = master.move_operators.get((lot_no, station))
    operator = texts.get("operator") or station_operator
    if (
        operator is None
        or not _OPERATOR.fullmatch(operator)
        or (operator not in master.operators and operator != station_operator)
    ):
        field = "operator" if "operator" in texts else "lot_no"
        return row.issue(field, E_REF_NOT_FOUND, "操作員不在名單中或該批沒有進出站紀錄")

    points = []
    for field in sheet.points:
        number = cast(Decimal, parsed[field])
        try:
            value = normalize_value(number, multiplier)
        except (ValueError, DecimalException):
            return row.issue(field, E_NOT_NUMERIC, "標準化數值超出可儲存範圍")
        if value < item.physical_min:
            return row.issue(
                field, E_OUT_OF_PHYSICAL_RANGE, f"量測值低於物理下限 {item.physical_min:g}"
            )
        if item.kind == "count" and number != number.to_integral_value():
            return row.issue(field, E_NOT_NUMERIC, "計數型量測值必須是整數")
        point = (
            None
            if item.kind == "count"
            else cast(int, parsed["point"])
            if "point" in parsed
            else int(field[1:])
            if field != "value"
            else None
        )
        if item.kind != "count" and point is None:
            return row.issue("item_code", E_REQUIRED_MISSING, "連續型檢驗需要量測點")
        points.append(
            Measurement(
                lot_no,
                station,
                item.code,
                cast(int, parsed["panel"]),
                point,
                machine_code,
                measured_at,
                operator,
                value,
                raw_row.line_no,
                row.column(field),
                str(row.raw(field)),
            )
        )
    return points
