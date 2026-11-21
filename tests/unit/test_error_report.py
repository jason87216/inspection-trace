from io import BytesIO

from openpyxl import load_workbook

from inspection_trace.ingestion.report import build_error_report
from inspection_trace.models import ImportErrorDetail


def test_excel_report_keeps_original_cell_text_literal() -> None:
    error = ImportErrorDetail(
        row_no=78, column="VAL", code="E_NOT_NUMERIC", message="量測值不是數字", raw_value="=1+1"
    )
    workbook = load_workbook(BytesIO(build_error_report([error])))
    try:
        sheet = workbook.active
        assert [cell.value for cell in sheet[2]] == [
            78,
            "VAL",
            "E_NOT_NUMERIC",
            "量測值不是數字",
            "=1+1",
        ]
        assert sheet["E2"].data_type == "s"
    finally:
        workbook.close()
