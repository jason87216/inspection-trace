"""Read CSV and Excel cells with physical source row numbers."""

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
from typing import Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from inspection_trace.ingestion.errors import E_FILE_ENCODING, E_FILE_FORMAT

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLUMNS = 64
type RawCell = str | int | float | bool | datetime | date | time | timedelta | None


@dataclass(frozen=True)
class CsvRow:
    line_no: int
    cells: tuple[str, ...]


@dataclass(frozen=True)
class CsvDocument:
    header: CsvRow
    rows: tuple[CsvRow, ...]


@dataclass(frozen=True)
class ExcelRow:
    line_no: int
    cells: tuple[RawCell, ...]


class FileReadError(ValueError):
    """A decoding or parsing failure, identified by a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CsvReadError(FileReadError):
    """A CSV decoding or parsing failure."""


def read_csv(data: bytes, *, encoding: Literal["cp950", "utf-8-sig"] = "utf-8-sig") -> CsvDocument:
    """Use the first nonempty record as the header and preserve raw data cells.

    Source line numbers are one-based and point to the start of each record,
    even when quoted fields span multiple physical lines.
    """
    try:
        decoded = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise CsvReadError(E_FILE_ENCODING, f"CSV is not valid {encoding}.") from exc

    reader = csv.reader(StringIO(decoded, newline=""), strict=True)
    rows: list[CsvRow] = []
    try:
        while True:
            line_no = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                break
            if line_no > MAX_ROWS or len(cells) > MAX_COLUMNS:
                raise CsvReadError(E_FILE_FORMAT, "CSV exceeds the supported row or column limit.")
            if cells:
                rows.append(CsvRow(line_no, tuple(cells)))
    except csv.Error as exc:
        raise CsvReadError(E_FILE_FORMAT, f"Invalid CSV record at line {line_no}.") from exc

    if not rows:
        raise CsvReadError(E_FILE_FORMAT, "CSV has no header.")
    return CsvDocument(rows[0], tuple(rows[1:]))


def read_csv_compatible(data: bytes) -> CsvDocument:
    try:
        return read_csv(data)
    except CsvReadError as exc:
        if exc.code != E_FILE_ENCODING:
            raise
        return read_csv(data, encoding="cp950")


def read_excel(data: bytes) -> tuple[ExcelRow, ...]:
    """Read the active sheet of a bounded, plain XLSX workbook."""
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise FileReadError(
                    E_FILE_FORMAT, "Excel workbook exceeds the expanded size limit."
                )
            for entry in entries:
                if entry.filename.lower().endswith((".xml", ".rels")):
                    content = archive.read(entry)
                    if b"\x00" in content or b"<!DOCTYPE" in content or b"<!ENTITY" in content:
                        raise FileReadError(E_FILE_FORMAT, "Unsupported workbook XML.")

        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
        try:
            sheet = workbook.active
            if sheet is None:
                raise FileReadError(E_FILE_FORMAT, "Excel workbook has no active worksheet.")
            if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                raise FileReadError(
                    E_FILE_FORMAT, "Excel exceeds the supported row or column limit."
                )
            sheet.reset_dimensions()
            rows = []
            for line_no, cells in enumerate(sheet.iter_rows(values_only=True), start=1):
                if line_no > MAX_ROWS or len(cells) > MAX_COLUMNS:
                    raise FileReadError(
                        E_FILE_FORMAT, "Excel exceeds the supported row or column limit."
                    )
                if any(cell is not None for cell in cells):
                    rows.append(ExcelRow(line_no, cells))
            return tuple(rows)
        finally:
            workbook.close()
    except FileReadError:
        raise
    except (
        BadZipFile,
        InvalidFileException,
        KeyError,
        OSError,
        ParseError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise FileReadError(E_FILE_FORMAT, "Unreadable Excel workbook.") from exc
