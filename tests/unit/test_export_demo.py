"""Database-free parts of scripts/export_demo.py."""

from pathlib import Path

from scripts.export_demo import build_preview, read_grid

SAMPLES = Path("samples/files")


def test_preview_keeps_first_rows_and_error_rows_with_file_row_numbers() -> None:
    grid = [[str(n), "x"] for n in range(1, 41)]

    preview = build_preview(grid, {35, 99})

    rows = [entry["row"] for entry in preview]
    assert rows == [*range(1, 13), 35]
    assert preview[-1]["cells"] == ["35", "x"]


def test_preview_pads_short_rows() -> None:
    preview = build_preview([["a", "b", "c"], ["d"]], set())

    assert preview[1]["cells"] == ["d", "", ""]


def test_read_grid_decodes_big5_legacy_csv_and_excel() -> None:
    legacy = read_grid(SAMPLES / "MES_OLD_C40_1151103.csv", "cp950")
    assert legacy[0][0] == "LOTNO"
    assert len(legacy) == 7

    sheet = read_grid(SAMPLES / "防焊_量測紀錄表_20261103.xlsx", None)
    assert any("合計" in row[0] for row in sheet)
