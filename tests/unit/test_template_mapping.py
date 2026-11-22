from io import BytesIO
from typing import Literal

import pytest
from openpyxl import Workbook

from inspection_trace.ingestion.mapping import HeaderMappingError
from inspection_trace.ingestion.templates import TemplateRules, map_template_file


def test_approved_column_map_uses_names_and_preserves_physical_rows() -> None:
    rules = TemplateRules(
        file_type="csv",
        header_row=2,
        column_map={
            "lot_no": "Batch",
            "panel": "Piece",
            "machine_code": "Device",
            "item_code": "Metric",
            "value": "Reading",
            "time": "Observed",
        },
    )
    sheet = map_template_file(
        "vendor.csv",
        b"Vendor statement\nReading,Batch,Device,Metric,Piece,Observed\n1,L1,M1,T1,2,2026-11-01\n",
        rules,
    )
    assert sheet.positions["value"] == 0
    assert sheet.header.line_no == 2
    assert sheet.rows[0].line_no == 3
    with pytest.raises(HeaderMappingError):
        map_template_file("vendor.csv", b"Vendor\nReading,Batch,Batch\n1,2,3\n", rules)


def test_xlsx_template_ignores_subtotal_rows_and_preserves_position() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Vendor cover"])
    sheet.append([])
    sheet.append(["Batch", "Piece", "Device", "Metric", "Reading", "Observed"])
    sheet.append(["L1", 1, "M1", "T1", 42, "2026-11-01"])
    sheet.append(["合計", 1])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    rules = TemplateRules(
        file_type="xlsx",
        header_row=3,
        column_map=dict(
            lot_no="Batch",
            panel="Piece",
            machine_code="Device",
            item_code="Metric",
            value="Reading",
            time="Observed",
        ),
    )
    result = map_template_file("vendor.xlsx", stream.getvalue(), rules)
    assert [row.line_no for row in result.rows] == [4]
    assert result.rows[0].cells[4] == 42


@pytest.mark.parametrize("file_type", ["csv", "xlsx"])
def test_template_keeps_measurements_with_a_total_label_in_the_note_column(
    file_type: Literal["csv", "xlsx"],
) -> None:
    rows = [
        ["Note", "Batch", "Piece", "Device", "Metric", "Reading", "Observed"],
        ["小計", "L1", "1", "M1", "T1", "42", "2026-11-01"],
        ["合計", "L1", "2", "M1", "T1", "43", "2026-11-01"],
        ["合計", "L1", "3", "M1", "T1", "", "2026-11-01"],
        ["合計", "", "2"],
    ]
    if file_type == "csv":
        data = "\n".join(",".join(row) for row in rows).encode()
    else:
        workbook = Workbook()
        for row in rows:
            workbook.active.append(row)
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        data = stream.getvalue()
    rules = TemplateRules(
        file_type=file_type,
        column_map=dict(
            lot_no="Batch",
            panel="Piece",
            machine_code="Device",
            item_code="Metric",
            value="Reading",
            time="Observed",
        ),
    )

    result = map_template_file(f"vendor.{file_type}", data, rules)

    assert [row.line_no for row in result.rows] == [2, 3, 4]
