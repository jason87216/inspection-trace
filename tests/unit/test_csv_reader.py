"""The CSV reader preserves source values and physical line numbers."""

import csv
from pathlib import Path

import pytest

from inspection_trace.ingestion.errors import E_FILE_ENCODING, E_FILE_FORMAT
from inspection_trace.ingestion.readers import CsvReadError, read_csv

SAMPLE_FILES = Path(__file__).resolve().parents[2] / "samples" / "files"


def test_reads_all_six_rows_from_clean_c40_sample() -> None:
    document = read_csv((SAMPLE_FILES / "MES_OLD_C40_1151103.csv").read_bytes(), encoding="cp950")

    assert document.header.cells == (
        "LOTNO",
        "PNL",
        "PT",
        "EQP",
        "ITEM",
        "VAL",
        "UOM",
        "DT",
        "TM",
        "OP",
    )
    assert [row.line_no for row in document.rows] == [2, 3, 4, 5, 6, 7]


def test_preserves_dirty_c40_values_at_their_original_lines() -> None:
    document = read_csv((SAMPLE_FILES / "MES_OLD_C40_1151102.csv").read_bytes(), encoding="cp950")
    value_column = document.header.cells.index("VAL")
    rows = {row.line_no: row for row in document.rows}

    assert rows[41].cells[value_column] == ""
    assert rows[78].cells[value_column] == "量不到"


@pytest.mark.parametrize("day", ["1151102", "1151103"])
def test_c40_sample_operator_matches_its_generated_measurement(day: str) -> None:
    document = read_csv((SAMPLE_FILES / f"MES_OLD_C40_{day}.csv").read_bytes(), encoding="cp950")
    truth_file = SAMPLE_FILES.parent / "truth" / "records.csv"
    with truth_file.open(encoding="utf-8", newline="") as source:
        expected = {
            (row["lot_no"], row["panel"], row["point"], row["item"]): row["operator"]
            for row in csv.DictReader(source)
            if row["station"] == "C40"
        }

    for row in document.rows:
        cells = dict(zip(document.header.cells, row.cells, strict=True))
        key = (cells["LOTNO"], cells["PNL"], cells["PT"], cells["ITEM"])
        assert cells["OP"] == expected[key]


def test_reads_utf8_machine_sample_without_converting_its_timestamp() -> None:
    document = read_csv((SAMPLE_FILES / "AOI_export_C50_20261102.csv").read_bytes())

    assert len(document.rows) == 140
    timestamp_column = document.header.cells.index("timestamp")
    assert document.rows[0].cells[timestamp_column] == "1793598997"


def test_keeps_start_line_of_quoted_multiline_cells_and_skips_blank_lines() -> None:
    document = read_csv(b'\xef\xbb\xbfNAME,VALUE\r\n\r\n"one\r\ntwo",1\r\nthree,2\r\n')

    assert document.header.cells == ("NAME", "VALUE")
    assert [(row.line_no, row.cells) for row in document.rows] == [
        (3, ("one\r\ntwo", "1")),
        (5, ("three", "2")),
    ]


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [(b"\xff", E_FILE_ENCODING), (b'NAME\n"unfinished', E_FILE_FORMAT), (b"\r\n", E_FILE_FORMAT)],
)
def test_csv_failure_has_a_stable_error_code(payload: bytes, expected_code: str) -> None:
    with pytest.raises(CsvReadError) as exc_info:
        read_csv(payload)

    assert exc_info.value.code == expected_code
