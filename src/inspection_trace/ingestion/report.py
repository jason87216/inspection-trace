"""Build the downloadable error report Excel."""

from collections.abc import Iterable
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from inspection_trace.models import ImportErrorDetail

REPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_error_report(errors: Iterable[ImportErrorDetail]) -> bytes:
    """Export saved error details and keep source text literal in Excel."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "匯入錯誤"
    sheet.append(("列", "欄位", "錯誤代碼", "說明", "原始值"))
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDE", (10, 18, 32, 60, 42), strict=True):
        sheet.column_dimensions[column].width = width

    for row, error in enumerate(errors, start=2):
        values = (error.row_no, error.column, error.code, error.message, error.raw_value)
        for column, value in enumerate(values, start=1):
            if isinstance(value, str):
                escaped = ILLEGAL_CHARACTERS_RE.sub(lambda match: f"\\u{ord(match[0]):04x}", value)
                if len(escaped) > 32767:
                    escaped = escaped[:32766] + "…"
                cell = sheet.cell(row, column, escaped)
                cell.data_type = "s"
            elif value is not None:
                sheet.cell(row, column, value)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
