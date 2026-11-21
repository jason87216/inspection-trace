"""In-memory document model shared by renderers and dirty operations.

Rows are objects, not indexes. Dirty operations keep references to rows and
columns; final row numbers and header texts are resolved only when the file is
written. That is what keeps answer keys correct after rows are inserted.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import get_column_letter


@dataclass(eq=False)
class Row:
    kind: str  # "data" | "blank" | "total"
    cells: dict[str, Any]
    record_ids: dict[str, int] = field(default_factory=dict)  # value column -> record id
    dirtied: bool = False  # at most one cell-level operation per row


@dataclass
class Roles:
    """Which column plays which part. Dirty operations work through roles."""

    lot: str
    machine: str
    item: str
    values: list[str]
    time: str | None
    time_style: str  # "excel" | "epoch" | "legacy"
    unit: str | None = None
    required: list[str] = field(default_factory=list)


@dataclass
class Sheet:
    name: str
    columns: list[str]
    headers: dict[str, str]
    rows: list[Row]
    preamble: list[list[Any]] = field(default_factory=list)
    merges: list[str] = field(default_factory=list)

    def header_row_number(self) -> int:
        return len(self.preamble) + 1

    def row_number(self, row: Row) -> int:
        for i, r in enumerate(self.rows):
            if r is row:
                return self.header_row_number() + 1 + i
        raise ValueError("row not in sheet")


@dataclass
class DocFile:
    filename: str
    source: str
    station: str
    fmt: str  # "csv" | "xlsx"
    encoding: str
    sheet: Sheet
    roles: Roles
    item_of_row: dict[int, str] = field(default_factory=dict)  # id(row) -> item code

    def item_code(self, row: Row) -> str:
        return self.item_of_row[id(row)]


def write_doc(doc: DocFile, path: Path) -> None:
    if doc.fmt == "csv":
        _write_csv(doc, path)
    else:
        _write_xlsx(doc, path)


def _csv_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def _write_csv(doc: DocFile, path: Path) -> None:
    sh = doc.sheet
    with path.open("w", newline="", encoding=doc.encoding) as f:
        w = csv.writer(f)
        for pre in sh.preamble:
            w.writerow([_csv_cell(v) for v in pre])
        w.writerow([sh.headers[c] for c in sh.columns])
        for row in sh.rows:
            w.writerow([_csv_cell(row.cells.get(c)) for c in sh.columns])


def _write_xlsx(doc: DocFile, path: Path) -> None:
    sh = doc.sheet
    wb = Workbook()
    ws = wb.active
    ws.title = sh.name
    for pre in sh.preamble:
        ws.append(pre)
    ws.append([sh.headers[c] for c in sh.columns])
    for row in sh.rows:
        ws.append([row.cells.get(c) for c in sh.columns])
    for rng in sh.merges:
        ws.merge_cells(rng)
    for r in ws.iter_rows(min_row=sh.header_row_number() + 1):
        for cell in r:
            if isinstance(cell.value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
    for i, c in enumerate(sh.columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, len(sh.headers[c]) * 2 + 2)
    wb.properties.creator = "inspection-trace generator"
    wb.save(path)
