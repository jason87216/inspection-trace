import csv
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generator.config import build_config
from generator.pipeline import run
from inspection_trace.ingestion.normalization import TAIPEI
from inspection_trace.scenario_schema import Preset, builtin, preview
from inspection_trace.seed import apply_seed, read_seed_tables

pytestmark = pytest.mark.integration


@pytest.fixture
def custom_preset() -> Preset:
    preset = builtin()
    raw = preset.config.model_dump()
    raw["scenario"].update(points_per_panel=2, qtime_limit_hours=1, operators=100)
    raw["scale"]["small"] = 2
    raw["parts"] = [{"part_no": "PN-CUSTOM", "name": "測試件", "line": "Z"}]
    raw["stations"] = [
        dict(raw["stations"][3], code="X10", name="自訂檢驗", machine_prefix="CHK", machines=1),
        dict(raw["stations"][5], code="X20", name="自訂出站", machine_prefix="END", machines=1),
        dict(raw["stations"][4], code="X30", name="自訂機檢", machine_prefix="AOI", machines=1),
    ]
    raw["items"] = [
        dict(raw["items"][0], code="ZWIDTH", station="X10", simulation_mean=48),
        dict(raw["items"][0], code="ZHEIGHT", name="ZWIDTH", station="X20"),
        dict(raw["items"][0], code="ZTHICK", name="ZHEIGHT", station="X20"),
        dict(raw["items"][1], code="ZCOUNT", station="X30"),
    ]
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
    return Preset.model_validate({"name": "自訂", "config": raw, "dirty": dirty})


def _upload(client: TestClient, path: Path) -> dict:
    response = client.post(
        "/imports",
        params={"file_name": path.name},
        headers={"Content-Type": "application/octet-stream"},
        content=path.read_bytes(),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    "start",
    ["1970-01-01T08:00:00", "1971-01-02T08:00:00", "1974-06-01T08:00:00", "2030-01-02T08:00:00"],
)
def test_custom_dataset_can_be_seeded_uploaded_traced_and_charted(
    start: str, custom_preset: Preset, tmp_path: Path, seeded_session: Session, client: TestClient
) -> None:
    custom_preset.config.scenario.start = start
    assert preview(custom_preset, "small").errors == []
    destination = tmp_path / "custom"
    config = build_config(custom_preset.config.model_dump(), custom_preset.dirty.model_dump())
    run(config, "small", 42, destination)
    truth = destination / "truth"
    apply_seed(seeded_session, read_seed_tables(truth))
    with (truth / "records.csv").open(encoding="utf-8") as stream:
        records = list(csv.DictReader(stream))
    assert any(int(row["operator"].split("-")[1]) > 12 for row in records)
    inserted_counts: dict[str, int] = {}
    files = destination / "files"
    for path in sorted([*files.glob("*.csv"), *files.glob("*.xlsx")]):
        result = _upload(client, path)
        assert result["status"] == "ACCEPT", result
        source = result["source_type"]
        inserted_counts[source] = inserted_counts.get(source, 0) + result["inserted_count"]
    assert inserted_counts == {"SRC_LEGACY": 12, "SRC_STATION": 24, "SRC_MACHINE": 40}

    machine = client.get("/records", params={"item_code": "ZCOUNT", "page_size": 100})
    assert machine.status_code == 200, machine.text
    assert {
        (row["lot_no"], row["panel"], datetime.fromisoformat(row["measured_at"]))
        for row in machine.json()["items"]
    } == {
        (
            row["lot_no"],
            int(row["panel"]),
            datetime.fromisoformat(row["measured_at"]).replace(tzinfo=TAIPEI),
        )
        for row in records
        if row["item"] == "ZCOUNT"
    }

    lot_no = f"L{datetime.fromisoformat(start):%y%m%d}Z001"
    trace = client.get(f"/lots/{lot_no}/trace").json()
    assert trace["links"][0]["parent_lot_no"].startswith("FILM-")
    detail = client.get(f"/lots/{lot_no}").json()
    assert detail["qtime"][0]["status"] == "over_limit"
    chart = client.get("/spc/ZWIDTH", params={"machine": "CHK-01", "baseline_points": 5})
    assert chart.status_code == 200
    assert len(chart.json()["points"]) == 12


def test_dirty_custom_dataset_matches_answer_coordinates_and_values(
    custom_preset: Preset, tmp_path: Path, seeded_session: Session, client: TestClient
) -> None:
    dirty = custom_preset.dirty
    dirty.files.clean_ratio = 0
    dirty.row_rates = dict.fromkeys(dirty.row_rates, 0)
    dirty.file_ops = dict.fromkeys(dirty.file_ops, 0)
    dirty.row_rates.update(SRC_STATION=1, SRC_LEGACY=1)
    dirty.ops["SRC_STATION"] = {"D_UNIT_MIL": 1}
    dirty.ops["SRC_LEGACY"] = {"D_NOT_NUMERIC": 1}
    assert preview(custom_preset, "small").errors == []
    destination = tmp_path / "dirty"
    config = build_config(custom_preset.config.model_dump(), dirty.model_dump())
    run(config, "small", 42, destination)
    apply_seed(seeded_session, read_seed_tables(destination / "truth"))

    normalized = {}
    injected = set()
    for answer_path in sorted((destination / "files").glob("*.answers.json")):
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        if not answer["injections"]:
            continue
        result = _upload(client, answer_path.with_name(answer["file"]))
        assert (result["status"], result["inserted_count"]) == (
            answer["expected_outcome"],
            answer["expected_record_count"],
        )
        expected_errors = set()
        for issue in answer["injections"]:
            injected.add((issue["category"], issue["op"]))
            if issue["category"] == "A":
                key = (answer["file"], issue["row"], int(issue["column"][1:]))
                normalized[key] = pytest.approx(
                    issue["expected_normalized_value"], abs=issue["tolerance"]
                )
            else:
                expected_errors.add(
                    (
                        issue["row"],
                        issue["column"],
                        issue["expected_error_code"],
                        issue["dirty_value"],
                    )
                )
        assert {
            (issue["row_no"], issue["column"], issue["code"], issue["raw_value"])
            for issue in result["errors"]
        } == expected_errors
    assert injected == {("A", "D_UNIT_MIL"), ("B", "D_NOT_NUMERIC")}

    response = client.get("/records", params={"station_code": "X20", "page_size": 100})
    assert response.status_code == 200
    assert {
        (row["file_name"], row["source_row_no"], row["point"]): row["value"]
        for row in response.json()["items"]
    } == normalized
