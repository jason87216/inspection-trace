"""Use a versioned column mapping for CSV or XLSX inputs."""

from pathlib import PurePath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from inspection_trace.ingestion.errors import E_FILE_FORMAT, E_HEADER_UNKNOWN
from inspection_trace.ingestion.mapping import (
    HeaderMappingError,
    ImportSheet,
    InputRow,
    _header_text,
    _match_headers,
)
from inspection_trace.ingestion.normalization import clean_text
from inspection_trace.ingestion.readers import FileReadError, read_csv_compatible, read_excel

SRC_TEMPLATE = "SRC_TEMPLATE"
REQUIRED_FIELDS = ("lot_no", "panel", "machine_code", "item_code", "value", "time")
FIELDS = (*REQUIRED_FIELDS, "point", "unit", "operator")
type FieldName = Literal[
    "lot_no", "panel", "machine_code", "item_code", "value", "time", "point", "unit", "operator"
]
type Header = Annotated[str, StringConstraints(min_length=1, max_length=100)]
type DateFormat = Literal["auto", "dmy", "mdy", "epoch"]


class TemplateRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_type: Literal["csv", "xlsx"]
    header_row: Annotated[int, Field(ge=1, le=30)] = 1
    column_map: dict[FieldName, Header]
    date_format: DateFormat = "auto"
    default_unit: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)] | None
    ) = None

    @model_validator(mode="after")
    def validate_mapping(self) -> "TemplateRules":
        if missing := set(REQUIRED_FIELDS) - self.column_map.keys():
            raise ValueError(f"缺少必填欄位對應：{', '.join(sorted(missing))}")
        names = list(self.column_map.values())
        if len(set(names)) != len(names) or any(not name.strip() for name in names):
            raise ValueError("每個欄位必須對應到不同且非空白的表頭")
        if self.default_unit is not None and "unit" in self.column_map:
            raise ValueError("來源已對應單位欄時，請留空預設單位")
        return self


def read_template_rows(file_name: str, data: bytes) -> tuple[InputRow, ...]:
    match PurePath(file_name).suffix.lower():
        case ".csv":
            document = read_csv_compatible(data)
            return (document.header, *document.rows)
        case ".xlsx":
            return read_excel(data)
        case _:
            raise FileReadError(E_FILE_FORMAT, "範本只適用於 CSV 或 XLSX")


def template_header(rows: tuple[InputRow, ...], line_no: int) -> InputRow:
    found = next((row for row in rows if row.line_no == line_no), None)
    if found is None:
        raise HeaderMappingError(E_HEADER_UNKNOWN, f"第 {line_no} 列沒有可用表頭")
    return found


def map_template_file(file_name: str, data: bytes, rules: TemplateRules) -> ImportSheet:
    if PurePath(file_name).suffix.lower() != f".{rules.file_type}":
        raise FileReadError(E_FILE_FORMAT, "檔案類型與範本不同")
    workbook = read_template_rows(file_name, data)
    header = template_header(workbook, rules.header_row)
    names = _header_text(header)
    mapping = _match_headers(
        names, {key: (rules.column_map[key],) for key in FIELDS if key in rules.column_map}
    )
    # A subtotal can contain counts or values, but has no measurement identifiers.
    record_columns = tuple(
        mapping.positions[field] for field in ("lot_no", "machine_code", "item_code", "time")
    )
    rows = tuple(
        row
        for row in workbook
        if row.line_no > header.line_no
        and any(cell not in ("", None) for cell in row.cells)
        and (
            clean_text(row.cells[0]) not in {"合計", "小計"}
            or any(
                0 < index < len(row.cells) and clean_text(row.cells[index])
                for index in record_columns
            )
        )
    )
    return ImportSheet(
        SRC_TEMPLATE,
        header,
        rows,
        mapping.positions,
        ("value",),
        mapping.unused_columns,
        rules.date_format,
        rules.default_unit,
    )
