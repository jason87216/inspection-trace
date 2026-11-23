import csv
import json
import tomllib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from generator.config import CONFIG_DIR, build_config
from generator.pipeline import run
from inspection_trace.ingestion.normalization import TAIPEI, parse_roc_day, parse_timestamp
from inspection_trace.main import app
from inspection_trace.scenario_schema import builtin, preview


def test_scenario_reports_encode_short_epoch_and_early_roc_year(tmp_path: Path) -> None:
    preset = builtin()
    preset.config.scenario.start = "1970-01-01T08:00:00"
    preset.config.scale.small = 1
    preset.config.stations = [preset.config.stations[index] for index in (4, 3)]
    for station in preset.config.stations:
        station.queue_hours = station.process_hours = (0, 0)
    preset.config.items = [preset.config.items[index] for index in (0, 1)]
    config = preset.config.model_dump()
    for event in config["events"].values():
        event["enabled"] = False
    preset.dirty.files.clean_ratio = 1
    preset.dirty.files.duplicate_ratio = 0
    destination = tmp_path / "early"
    run(build_config(config, preset.dirty.model_dump()), "small", 42, destination)

    machine = next((destination / "files").glob("AOI*.csv"))
    with machine.open(encoding="utf-8", newline="") as stream:
        timestamp = next(csv.DictReader(stream))["timestamp"]
    assert len(timestamp) == 9 and timestamp.startswith("000")
    assert parse_timestamp("000000000", machine=True) == datetime(1970, 1, 1, 8, tzinfo=TAIPEI)
    with (destination / "truth" / "records.csv").open(encoding="utf-8", newline="") as stream:
        expected = min(
            datetime.fromisoformat(row["measured_at"]).replace(tzinfo=TAIPEI)
            for row in csv.DictReader(stream)
            if row["station"] == "C50"
        )
    assert parse_timestamp(timestamp, machine=True) == expected
    legacy = next((destination / "files").glob("MES_OLD*.csv"))
    with legacy.open(encoding="cp950", newline="") as stream:
        assert parse_roc_day(next(csv.DictReader(stream))["DT"]) == datetime(1970, 1, 1).date()


def test_scenario_preview_checks_the_taipei_unix_epoch_boundary() -> None:
    preset = builtin()
    preset.config.scenario.start = "1970-01-01T07:59:59"
    assert preview(preset, "small").errors
    preset.config.scenario.start = "1970-01-01T08:00:00"
    assert preview(preset, "small").errors == []


def test_generator_can_run_without_abf_codes(tmp_path: Path) -> None:
    raw = tomllib.loads((CONFIG_DIR / "scenario.toml").read_text(encoding="utf-8"))
    dirty = tomllib.loads((CONFIG_DIR / "dirty.toml").read_text(encoding="utf-8"))
    raw["stations"] = [
        dict(raw["stations"][3], code="X10", name="測試站", machine_prefix="CHK"),
        dict(raw["stations"][0], code="X20", name="出站", machine_prefix="END"),
    ]
    raw["items"] = [dict(raw["items"][0], station="X10", simulation_mean=48)]
    raw["scenario"].update(points_per_panel=2)
    raw["materials"] = {"kinds": [{"code": "FILM", "lots_per_material": 2}]}
    for event in raw["events"].values():
        event["enabled"] = False
    raw["events"]["qtime_over"].update(
        enabled=True, from_station="X10", to_station="X20", affected_items=[], probability=0
    )
    raw["events"]["oos_spike"].update(
        enabled=True, items=["CORE_LW"], probability_per_value=1, sigma=0.1
    )
    dirty["files"].update(clean_ratio=0, duplicate_ratio=0)
    dirty["row_rates"] = dict.fromkeys(dirty["row_rates"], 0)
    dirty["file_ops"] = dict.fromkeys(dirty["file_ops"], 0)
    config = build_config(raw, dirty)
    destination = tmp_path / "dataset"
    summary = run(config, "small", 42, destination)
    assert summary["records"] == 48
    assert summary["injections"] == {}
    truth = destination / "truth"
    events = json.loads((truth / "events.answers.json").read_text(encoding="utf-8"))
    assert len(events) == 48
    assert all(event["value"] < 45 or event["value"] > 55 for event in events)
    with (truth / "materials.csv").open(encoding="utf-8") as source:
        assert {row["kind"] for row in csv.DictReader(source)} == {"FILM"}
    with (truth / "qtime_rules.csv").open(encoding="utf-8") as source:
        rule = next(csv.DictReader(source))
        assert (rule["from_station_code"], rule["to_station_code"]) == ("X10", "X20")
    assert list((destination / "files").glob("MES_OLD_X10_*.csv"))


def test_presets_validate_save_generate_and_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSPECTION_SCENARIO_ROOT", str(tmp_path))
    with TestClient(app) as client:
        path = "/scenarios/builtin-abf"
        original = client.get(path).json()
        assert original["id"] == "builtin-abf"
        custom = client.get(path).json()["preset"]
        custom["name"] = "測試情境"
        custom["config"]["scenario"]["points_per_panel"] = 3
        custom["config"]["scale"]["small"] = 2
        imported = client.post("/scenarios/import", json={"text": json.dumps(custom)})
        assert imported.json() == custom
        assert client.put(path, json={"revision": 0, "preset": custom}).status_code == 409
        assert client.delete(path, params={"revision": 0}).status_code == 409
        result = client.post("/scenarios/validate", json={"preset": custom}).json()
        assert result["errors"] == [] and result["lots"] == 2
        created = client.post("/scenarios", json=custom)
        assert created.status_code == 201, created.text
        saved = created.json()
        custom_path = f"/scenarios/{saved['id']}"
        assert client.put(custom_path, json={"revision": 0, "preset": custom}).status_code == 409
        assert client.get(path).json() == original

        settings = {"revision": 1, "seed": 17, "confirm": True}
        assert (
            client.post(f"{custom_path}/runs", json={**settings, "confirm": False}).status_code
            == 422
        )
        first = client.post(f"{custom_path}/runs", json=settings)
        assert first.status_code == 201, first.text
        next_run = client.post(f"{custom_path}/runs", json=settings).json()
        result = first.json()
        assert result["id"] != next_run["id"] and result["summary"] == next_run["summary"]
        with (
            ZipFile(BytesIO(client.get(result["download_url"]).content)) as archive,
            ZipFile(BytesIO(client.get(next_run["download_url"]).content)) as repeated,
        ):
            assert {"truth/records.csv", "manifest.csv", "summary.json", "preset.json"} <= set(
                archive.namelist()
            )
            assert any(
                name.startswith("files/") and name.endswith(".answers.json")
                for name in archive.namelist()
            )
            assert archive.read("truth/records.csv") == repeated.read("truth/records.csv")
            assert json.loads(archive.read("preset.json")) == custom

        assert client.delete(custom_path, params={"revision": 1}).status_code == 204
        assert client.get(custom_path).status_code == 404
        assert client.get(result["download_url"]).status_code == 200


def test_toml_import_preview_blocks_missing_station_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSPECTION_SCENARIO_ROOT", str(tmp_path))
    with TestClient(app) as client:
        raw = (CONFIG_DIR / "scenario.toml").read_text(encoding="utf-8")
        imported = client.post("/scenarios/import", json={"name": "TOML 草稿", "text": raw})
        assert imported.status_code == 200, imported.text
        preset = imported.json()
        preset["config"]["events"]["qtime_over"]["from_station"] = "MISSING"
        preview = client.post("/scenarios/validate", json={"preset": preset}).json()
        assert any("Q-time" in error for error in preview["errors"])
        assert client.post("/scenarios", json=preset).status_code == 422
        assert len(client.get("/scenarios").json()) == 1
