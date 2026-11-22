"""Checks that the generator's files and answer keys agree with each other."""

from __future__ import annotations

import csv
import json
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from generator.config import load_config
from generator.pipeline import run

SEED = 42


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def out(tmp_path_factory, cfg) -> Path:
    path = tmp_path_factory.mktemp("gen") / "medium"
    run(cfg, "medium", SEED, path)
    return path


def answers(out: Path) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((out / "files").glob("*.answers.json"))
    ]


def read_grid(path: Path, encoding: str | None) -> list[list[Any]]:
    """Return the file as a list of rows (1-based row n is grid[n-1])."""
    if path.suffix == ".csv":
        with path.open(newline="", encoding=encoding) as f:
            return [row for row in csv.reader(f)]
    ws = load_workbook(path).active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def same(cell: Any, expected: Any) -> bool:
    if expected is None:
        return cell in (None, "")
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(cell) - float(expected)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(cell) == str(expected)


# ---------------------------------------------------------------- answer keys
def test_every_injection_points_at_the_dirty_cell(out):
    checked = 0
    for a in answers(out):
        if a["expected_outcome"] == "DUPLICATE_FILE":
            continue
        grid = read_grid(out / "files" / a["file"], a["encoding"])
        header = grid[a["header_row"] - 1]
        for inj in a["injections"]:
            row = grid[inj["row"] - 1]
            if inj["op"] == "D_HEADER_ALIAS":
                assert inj["row"] == a["header_row"]
                assert inj["dirty_value"] in header
            elif inj["op"] == "D_DUP_ROW":
                assert row == grid[inj["row"] - 2], a["file"]
            elif inj["op"] == "D_LAYOUT_NOISE":
                assert all(v in (None, "") for v in row) or row[0] == "小計"
            else:
                col = header.index(inj["column"])
                assert same(row[col], inj["dirty_value"]), (a["file"], inj)
            checked += 1
    assert checked > 100


def test_outcome_is_reject_exactly_when_category_b_exists(out):
    for a in answers(out):
        if a["expected_outcome"] == "DUPLICATE_FILE":
            continue
        has_b = any(i["category"] == "B" for i in a["injections"])
        assert (a["expected_outcome"] == "REJECT") == has_b
        assert (a["expected_record_count"] == 0) == has_b


def test_clean_files_have_no_injections_and_full_record_count(out):
    clean = [a for a in answers(out) if not a["is_dirty"] and "duplicate_of" not in a]
    assert clean, "expected some clean files"
    for a in clean:
        assert a["injections"] == []
        assert a["expected_outcome"] == "ACCEPT"
        assert a["expected_record_count"] > 0


def test_record_counts_add_up_to_truth(out):
    truth = list(csv.DictReader((out / "truth" / "records.csv").open(encoding="utf-8")))
    rendered_total = 0
    for a in answers(out):
        if "duplicate_of" in a:
            continue
        grid = read_grid(out / "files" / a["file"], a["encoding"])
        header = grid[a["header_row"] - 1]
        body = grid[a["header_row"] :]
        dup_rows = {i["row"] for i in a["injections"] if i["op"] == "D_DUP_ROW"}
        noise_rows = {i["row"] for i in a["injections"] if i["op"] == "D_LAYOUT_NOISE"}
        value_cols = [i for i, h in enumerate(header) if h and re.fullmatch(r"P\d", str(h))]
        for n, row in enumerate(body, start=a["header_row"] + 1):
            if n in dup_rows or n in noise_rows or not any(v not in (None, "") for v in row):
                continue
            if str(row[0]).startswith(("合計", "小計")):
                continue
            # wide files carry several values per row; long files one
            rendered_total += len(value_cols) if value_cols else 1
    # blanked value cells still occupy their slot in the wide layout
    assert rendered_total == len(truth)


def test_duplicate_files_are_byte_identical(out):
    dups = [a for a in answers(out) if a.get("duplicate_of")]
    assert dups
    for a in dups:
        assert (out / "files" / a["file"]).read_bytes() == (
            out / "files" / a["duplicate_of"]
        ).read_bytes()


# ------------------------------------------------------------- operations
def test_lot_numbers_follow_pattern_and_typos_break_it(out, cfg):
    pattern = re.compile(cfg.scenario["lot_pattern"])
    lots = list(csv.DictReader((out / "truth" / "lots.csv").open(encoding="utf-8")))
    assert all(pattern.fullmatch(lot["lot_no"]) for lot in lots)
    typos = [i for a in answers(out) for i in a["injections"] if i["op"] == "D_LOT_TYPO"]
    assert typos
    assert not any(pattern.fullmatch(i["dirty_value"]) for i in typos)


def test_unit_mil_round_trips(out):
    mils = [i for a in answers(out) for i in a["injections"] if i["op"] == "D_UNIT_MIL"]
    assert mils
    for i in mils:
        back = float(i["dirty_value"]) * 25.4
        assert abs(back - i["expected_normalized_value"]) <= i["tolerance"]


def test_fullwidth_values_normalize_back(out):
    fw = [i for a in answers(out) for i in a["injections"] if i["op"] == "D_FULLWIDTH"]
    assert fw
    for i in fw:
        half = i["dirty_value"].translate(str.maketrans("０１２３４５６７８９．－", "0123456789.-"))
        assert float(half) == pytest.approx(i["expected_normalized_value"])


def test_category_c_values_stay_plausible(out, cfg):
    for a in answers(out):
        for i in a["injections"]:
            if i["op"] == "D_TRANSPOSE":
                lo, hi = None, None
                for item in cfg.items:
                    if item.station == a["station"] and item.kind == "continuous":
                        lo = item.lsl if item.lsl is not None else item.physical_min
                        hi = item.usl
                        if lo <= float(i["dirty_value"]) <= hi:
                            break
                assert lo is not None and lo <= float(i["dirty_value"]) <= hi
            if i["op"] == "D_WRONG_MACHINE":
                assert i["dirty_value"] != i["original_value"]
                assert i["dirty_value"].split("-")[0] == i["original_value"].split("-")[0]


# ------------------------------------------------------------------ events
def _records(out: Path) -> list[dict[str, str]]:
    return list(csv.DictReader((out / "truth" / "records.csv").open(encoding="utf-8")))


def test_qtime_events_match_lot_qtime(out, cfg):
    limit = cfg.scenario["qtime_limit_hours"]
    lots = {r["lot_no"]: r for r in csv.DictReader((out / "truth" / "lots.csv").open(encoding="utf-8"))}
    events = json.loads((out / "truth" / "events.answers.json").read_text(encoding="utf-8"))
    flagged = {e["lot_no"] for e in events if e["event"] == "EV_QTIME_OVER"}
    for lot_no, lot in lots.items():
        if lot["parent"]:
            continue
        over = float(lot["qtime_hours"]) > limit
        assert over == (lot_no in flagged), lot_no


def test_drift_lowers_via_diameter_within_a_segment(out):
    events = json.loads((out / "truth" / "events.answers.json").read_text(encoding="utf-8"))
    drift = [e for e in events if e["event"] == "EV_DRIFT" and len(e["lots"]) >= 6]
    assert drift
    by_lot = defaultdict(list)
    for r in _records(out):
        if r["item"] == "VIA_TOP_D" and r["machine"] == drift[0]["machine"]:
            by_lot[r["lot_no"]].append(float(r["value"]))
    seg = drift[0]["lots"]
    early = statistics.mean(v for lot in seg[:2] for v in by_lot[lot])
    late = statistics.mean(v for lot in seg[-2:] for v in by_lot[lot])
    assert late < early - 1.0


def test_material_shift_raises_abf_thickness(out, cfg):
    events = json.loads((out / "truth" / "events.answers.json").read_text(encoding="utf-8"))
    shift = next(e for e in events if e["event"] == "EV_MAT_SHIFT")
    affected = set(shift["lots"])
    before, after = [], []
    for r in _records(out):
        if r["item"] == "ABF_THK":
            (after if r["lot_no"] in affected else before).append(float(r["value"]))
    assert statistics.mean(after) - statistics.mean(before) == pytest.approx(
        cfg.events["material_shift"]["delta"], abs=0.5
    )


# ------------------------------------------------------------- determinism
def test_same_seed_same_output(tmp_path, cfg):
    a, b = tmp_path / "a", tmp_path / "b"
    run(cfg, "small", 7, a)
    run(cfg, "small", 7, b)
    assert (a / "truth" / "records.csv").read_bytes() == (b / "truth" / "records.csv").read_bytes()
    names = sorted(p.name for p in (a / "files").iterdir())
    assert names == sorted(p.name for p in (b / "files").iterdir())
    for name in names:
        pa, pb = a / "files" / name, b / "files" / name
        if name.endswith(".xlsx"):
            assert read_grid(pa, None) == read_grid(pb, None)
        else:
            assert pa.read_bytes() == pb.read_bytes()


def test_generator_refuses_to_replace_existing_output(tmp_path, cfg, monkeypatch):
    output = tmp_path / "existing"
    scenarios = output / "scenarios"
    scenarios.mkdir(parents=True)
    preset = scenarios / "preset.json"
    preset.write_text('{"name":"Keep this preset"}', encoding="utf-8")
    summary = output / "summary.json"
    summary.write_text('{"seed":42}', encoding="utf-8")

    def unexpected_delete(path):
        pytest.fail(f"Generator tried to delete {path}")

    monkeypatch.setattr(shutil, "rmtree", unexpected_delete)
    with pytest.raises(FileExistsError, match="not empty"):
        run(cfg, "small", SEED, output)
    assert preset.read_text(encoding="utf-8") == '{"name":"Keep this preset"}'
    assert summary.read_text(encoding="utf-8") == '{"seed":42}'
