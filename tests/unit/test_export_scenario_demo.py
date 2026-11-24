"""scripts/export_scenario_demo.py: built-in preset previews and documented edits."""

from scripts.export_scenario_demo import export


def test_builtin_preset_is_valid_at_every_scale() -> None:
    data = export()

    assert set(data["previews"]) == {"small", "medium", "large"}
    assert all(not result["errors"] for result in data["previews"].values())
    assert [data["previews"][s]["lots"] for s in ("small", "medium", "large")] == [8, 60, 600]


def test_each_edit_shows_the_check_it_documents() -> None:
    edits = {row["id"]: row["preview"] for row in export()["edits"]}

    assert "抽樣片數不可超過每批總片數。" in edits["sample_over_lot"]["errors"]
    assert any(
        "SR_THK" in error and "規格上限" in error for error in edits["target_above_usl"]["errors"]
    )
    assert any("200,000" in error for error in edits["too_many_records"]["errors"])
    assert not edits["material_shift_off"]["errors"]
    assert "所選批數尚未到達原料突變的開始批次。" not in edits["material_shift_off"]["warnings"]
    assert "所選批數尚未到達原料突變的開始批次。" in export()["previews"]["small"]["warnings"]
