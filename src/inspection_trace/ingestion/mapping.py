"""Map raw header names to canonical column names via an alias dictionary."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath
from re import fullmatch

from inspection_trace.ingestion.errors import (
    E_FILE_FORMAT,
    E_HEADER_CONFLICT,
    E_HEADER_UNKNOWN,
)
from inspection_trace.ingestion.readers import (
    CsvRow,
    ExcelRow,
    FileReadError,
    read_csv_compatible,
    read_excel,
)

SRC_LEGACY = "SRC_LEGACY"
SRC_MACHINE = "SRC_MACHINE"
SRC_STATION = "SRC_STATION"
SRC_MANUAL = "SRC_MANUAL"

_C40_ALIASES = {
    "lot_no": ("LOTNO", "LOT_NO", "LOT"),
    "panel": ("PNL",),
    "point": ("PT",),
    "machine_code": ("EQP", "EQPID", "MC"),
    "item_code": ("ITEM",),
    "value": ("VAL", "VALUE", "DATA"),
    "unit": ("UOM",),
    "date": ("DT",),
    "time": ("TM",),
    "operator": ("OP",),
}
_MACHINE_ALIASES = {
    "lot_no": ("lot_id", "LotID", "LOT_NO", "lot no"),
    "panel": ("panel_no",),
    "machine_code": ("equipment_id", "eqp_id", "machine", "EQP"),
    "item_code": ("inspection_item",),
    "value": ("value", "result", "count"),
    "time": ("timestamp", "ts", "time"),
}
_STATION_ALIASES = {
    "lot_no": ("批號", "Lot", "LOT NO", "批次"),
    "panel": ("片號", "Panel", "片"),
    "machine_code": ("機台", "設備", "機台編號"),
    "item_code": ("檢驗項目", "項目", "量測項目"),
    "unit": ("單位",),
    "time": ("量測時間", "日期時間", "時間"),
    "operator": ("作業員",),
}
_MANUAL_ALIASES = {
    "lot_no": ("批號", "批次號碼", "Lot No."),
    "panel": ("片號",),
    "point": ("量測點", "點位", "Point"),
    "machine_code": ("機台", "設備"),
    "item_code": ("項目",),
    "value": ("數值", "量測值", "讀值"),
    "unit": ("單位",),
    "time": ("量測時間",),
}
type InputRow = CsvRow | ExcelRow


class HeaderMappingError(ValueError):
    """An invalid header with a stable code for the import report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UnusedColumn:
    """Original name and one-based column number for an import warning."""

    column_no: int
    header: str


@dataclass(frozen=True)
class C40HeaderReport:
    positions: dict[str, int]
    unused_columns: tuple[UnusedColumn, ...]


@dataclass(frozen=True)
class ImportSheet:
    source_type: str
    header: InputRow
    rows: tuple[InputRow, ...]
    positions: dict[str, int]
    points: tuple[str, ...]
    unused_columns: tuple[UnusedColumn, ...]
    date_format: str = "auto"
    default_unit: str | None = None


def map_c40_headers(headers: Sequence[str]) -> dict[str, int]:
    """Return each C40 field's zero-based position for indexing raw cells."""
    return inspect_c40_headers(headers).positions


def inspect_c40_headers(headers: Sequence[str]) -> C40HeaderReport:
    """Match all required headers exactly and report unused columns separately."""
    return _match_headers(headers, _C40_ALIASES)


def _match_headers(
    headers: Sequence[str], aliases: dict[str, tuple[str, ...]], *, ignored: tuple[str, ...] = ()
) -> C40HeaderReport:
    by_header = {alias: field for field, names in aliases.items() for alias in names}
    positions: dict[str, int] = {}
    unused_columns: list[UnusedColumn] = []
    for position, header in enumerate(headers):
        field = by_header.get(header)
        if field is None:
            if header not in ignored:
                unused_columns.append(UnusedColumn(column_no=position + 1, header=header))
            continue
        if field in positions:
            previous = positions[field]
            raise HeaderMappingError(
                E_HEADER_CONFLICT,
                f"Columns {previous + 1} ({headers[previous]!r}) and "
                f"{position + 1} ({header!r}) both map to {field!r}.",
            )
        positions[field] = position

    missing = [field for field in aliases if field not in positions]
    if missing:
        expected = ", ".join(f"{field} ({'/'.join(aliases[field])})" for field in missing)
        raise HeaderMappingError(E_HEADER_UNKNOWN, f"Missing required columns: {expected}.")
    ordered = {field: positions[field] for field in aliases}
    return C40HeaderReport(positions=ordered, unused_columns=tuple(unused_columns))


def _header_text(row: InputRow) -> tuple[str, ...]:
    return tuple(str(cell) if cell is not None else "" for cell in row.cells)


def _header_score(headers: Sequence[str], aliases: dict[str, tuple[str, ...]]) -> int:
    return sum(any(name in headers for name in names) for names in aliases.values())


def map_import_file(file_name: str, data: bytes) -> ImportSheet:
    """Detect a supported source from its actual headers and retain original coordinates."""
    extension = PurePath(file_name).suffix.lower()
    if extension == ".csv":
        document = read_csv_compatible(data)
        headers = document.header.cells
        if _header_score(headers, _C40_ALIASES) > _header_score(headers, _MACHINE_ALIASES):
            source, report = SRC_LEGACY, inspect_c40_headers(headers)
        else:
            source, report = SRC_MACHINE, _match_headers(headers, _MACHINE_ALIASES)
        return ImportSheet(
            source,
            document.header,
            document.rows,
            report.positions,
            ("value",),
            report.unused_columns,
        )
    if extension != ".xlsx":
        raise FileReadError(E_FILE_FORMAT, "Expected a .csv or .xlsx file.")

    workbook_rows = read_excel(data)
    candidates = [
        (_header_score(headers := _header_text(row), aliases), source, row)
        for row in workbook_rows
        if row.line_no <= 12
        for source, aliases in ((SRC_MANUAL, _MANUAL_ALIASES), (SRC_STATION, _STATION_ALIASES))
    ]
    score, source, header = max(candidates, key=lambda entry: entry[0], default=(0, "", None))
    if not score or header is None:
        raise HeaderMappingError(E_HEADER_UNKNOWN, "No inspection header found in the workbook.")
    headers = _header_text(header)
    if source == SRC_STATION:
        point_numbers = sorted(
            {int(name[1:]) for name in headers if fullmatch(r"P[1-9][0-9]?", name)}
        )
        if not point_numbers or point_numbers != list(range(1, point_numbers[-1] + 1)):
            raise HeaderMappingError(
                E_HEADER_UNKNOWN, "Expected contiguous measurement columns P1, P2, …."
            )
        points = tuple(f"P{number}" for number in point_numbers)
        report = _match_headers(
            headers, {**_STATION_ALIASES, **{name: (name,) for name in points}}, ignored=("班別",)
        )
    else:
        points = ("value",)
        report = _match_headers(headers, _MANUAL_ALIASES, ignored=("備註",))
    rows = tuple(
        row
        for row in workbook_rows
        if row.line_no > header.line_no and str(row.cells[0]).strip() not in {"合計", "小計"}
    )
    return ImportSheet(source, header, rows, report.positions, points, report.unused_columns)
