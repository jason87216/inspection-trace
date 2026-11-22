import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generator.config import build_config
from generator.pipeline import run
from inspection_trace.scenario_schema import builtin, preview
from inspection_trace.seed import apply_seed, read_seed_tables

pytestmark = pytest.mark.integration


def test_custom_dataset_can_be_seeded_uploaded_traced_and_charted(
    tmp_path: Path, seeded_session: Session, client: TestClient
) -> None:
    preset = builtin()
    raw = preset.config.model_dump()
    raw["scenario"].update(
        start="2030-01-02T08:00:00", points_per_panel=2, qtime_limit_hours=1, operators=100
    )
    raw["scale"]["small"] = 2
    raw["parts"] = [{"part_no": "PN-CUSTOM", "name": "測試件", "line": "Z"}]
    raw["stations"] = [
        dict(raw["stations"][3], code="X10", name="自訂檢驗", machine_prefix="CHK", machines=1),
        dict(raw["stations"][0], code="X20", name="自訂出站", machine_prefix="END", machines=1),
    ]
    raw["items"] = [dict(raw["items"][0], code="WIDTH", station="X10", simulation_mean=48)]
    raw["materials"] = {"kinds": [{"code": "FILM", "lots_per_material": 1}]}
    for event in raw["events"].values():
        event["enabled"] = False
    raw["events"]["qtime_over"].update(
        enabled=True,
        from_station="X10",
        to_station="X20",
        affected_items=[],
        extra_wait_hours=[3, 3],
        probability=1,
    )
    dirty = preset.dirty.model_dump()
    dirty["files"].update(clean_ratio=1, duplicate_ratio=0)
    custom = type(preset).model_validate({"name": "自訂", "config": raw, "dirty": dirty})
    assert preview(custom, "small").errors == []
    destination = tmp_path / "custom"
    run(build_config(raw, dirty), "small", 42, destination)
    truth = destination / "truth"
    apply_seed(seeded_session, read_seed_tables(truth))
    with (truth / "records.csv").open(encoding="utf-8") as stream:
        assert any(int(row["operator"].split("-")[1]) > 12 for row in csv.DictReader(stream))
    for path in (destination / "files").glob("*.csv"):
        response = client.post(
            "/imports",
            params={"file_name": path.name},
            headers={"Content-Type": "application/octet-stream"},
            content=path.read_bytes(),
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "ACCEPT", response.text

    trace = client.get("/lots/L300102Z001/trace").json()
    assert trace["links"][0]["parent_lot_no"].startswith("FILM-")
    detail = client.get("/lots/L300102Z001").json()
    assert detail["qtime"][0]["status"] == "over_limit"
    chart = client.get("/spc/WIDTH", params={"machine": "CHK-01", "baseline_points": 5})
    assert chart.status_code == 200
    assert len(chart.json()["points"]) == 12
