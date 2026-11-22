"""Read the fixed-layout, text-layer lab report used by the simulator."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from inspection_trace.ingestion.errors import E_FILE_FORMAT
from inspection_trace.ingestion.mapping import ImportSheet
from inspection_trace.ingestion.readers import MAX_FILE_BYTES, CsvRow, FileReadError

SRC_PDF = "SRC_PDF"
TITLE = "INSPECTION TRACE LAB REPORT V1"
FOOTER = "END LAB REPORT"
FIELDS = (
    "lot_no",
    "panel",
    "point",
    "machine_code",
    "item_code",
    "value",
    "unit",
    "time",
    "operator",
)
HEADERS = ("LOT", "PANEL", "POINT", "MACHINE", "ITEM", "VALUE", "UNIT", "MEASURED_AT", "OPERATOR")
MAX_PAGES = 10
MAX_PAGE_BYTES = 512 * 1024
MAX_DATA_ROWS = 40


def map_lab_pdf(data: bytes) -> ImportSheet:
    """Convert a known lab layout to raw cells for the same validation as CSV/XLSX."""
    if len(data) > MAX_FILE_BYTES or not data.startswith(b"%PDF-"):
        raise FileReadError(E_FILE_FORMAT, "檔案不是可讀取的 PDF")
    rows = []
    offset = 0
    try:
        with PdfReader(BytesIO(data), strict=True, xref_stream_maximum_entries=5000) as reader:
            if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
                raise FileReadError(E_FILE_FORMAT, "PDF 必須未加密且介於 1 至 10 頁")
            for number, page in enumerate(reader.pages, start=1):
                contents = page.get_contents()
                if contents is None or len(contents.get_data()) > MAX_PAGE_BYTES:
                    raise FileReadError(E_FILE_FORMAT, f"PDF 第 {number} 頁沒有文字或超過內容限制")
                text = page.extract_text()
                if len(text) > MAX_PAGE_BYTES:
                    raise FileReadError(E_FILE_FORMAT, f"PDF 第 {number} 頁的文字超過限制")
                lines = text.strip().splitlines()
                if (
                    len(lines) < 4
                    or len(lines) > MAX_DATA_ROWS + 3
                    or lines[0].strip() != TITLE
                    or tuple(cell.strip() for cell in lines[1].split("|")) != HEADERS
                    or lines[-1].strip() != FOOTER
                ):
                    raise FileReadError(E_FILE_FORMAT, f"PDF 第 {number} 頁不符合化驗報告 v1 版面")
                for line_no, line in enumerate(lines[2:-1], start=3):
                    cells = tuple(cell.strip() for cell in line.split("|"))
                    if len(cells) != len(HEADERS):
                        raise FileReadError(
                            E_FILE_FORMAT, f"PDF 第 {number} 頁第 {line_no} 列欄數錯誤"
                        )
                    rows.append(CsvRow(offset + line_no, cells))
                offset += len(lines)
    except FileReadError:
        raise
    except (
        PyPdfError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
    ) as exc:
        raise FileReadError(E_FILE_FORMAT, "PDF 無法擷取固定版面的文字") from exc
    return ImportSheet(
        SRC_PDF,
        CsvRow(2, HEADERS),
        tuple(rows),
        {field: index for index, field in enumerate(FIELDS)},
        ("value",),
        (),
    )
