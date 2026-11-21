"""End-to-end: simulate -> truth -> render -> dirty -> files + answer keys."""

from __future__ import annotations

import csv
import json
import random
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from generator.config import Config
from generator.dirty import Dirtier, Injection
from generator.master import build_master
from generator.render import render_all
from generator.simulate import simulate, write_truth
from generator.table import DocFile, write_doc


def _json(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def build_answers(doc: DocFile, injections: list[Injection], is_dirty: bool) -> dict[str, Any]:
    sheet = doc.sheet
    has_b = any(i.category == "B" for i in injections)
    record_count = sum(len(r.record_ids) for r in sheet.rows if r.kind == "data")
    return {
        "file": doc.filename,
        "source": doc.source,
        "station": doc.station,
        "format": doc.fmt,
        "encoding": doc.encoding or None,
        "header_row": sheet.header_row_number(),
        "is_dirty": is_dirty,
        "expected_outcome": "REJECT" if has_b else "ACCEPT",
        "expected_record_count": 0 if has_b else record_count,
        "injections": [
            {
                "op": i.op,
                "category": i.category,
                "expected_error_code": i.expected_error_code,
                "row": sheet.header_row_number() if i.row is None else sheet.row_number(i.row),
                "column": None if i.column is None else sheet.headers[i.column],
                "column_key": i.column,
                "original_value": _json(i.original_value),
                "dirty_value": _json(i.dirty_value),
                "expected_normalized_value": _json(i.expected_normalized_value),
                "tolerance": i.tolerance,
                "note": i.note,
            }
            for i in sorted(
                injections,
                key=lambda i: (
                    sheet.header_row_number() if i.row is None else sheet.row_number(i.row)
                ),
            )
        ],
    }


def _clear_previous_output(out: Path) -> None:
    """Delete `out` only if it is empty or an earlier generator output (has summary.json).

    Anything else is refused, so a wrong --out (e.g. "." or a source folder) never wipes it.
    """
    if not out.exists():
        return
    if not out.is_dir():
        raise FileExistsError(f"--out {out} is a file, not a directory")
    if any(out.iterdir()) and not (out / "summary.json").is_file():
        raise FileExistsError(
            f"--out {out} is not empty and has no summary.json; "
            "refusing to delete it. Choose an empty or new directory."
        )
    shutil.rmtree(out)


def run(cfg: Config, scale: str, seed: int, out: Path) -> dict[str, Any]:
    lot_count = cfg.scale[scale]
    sim_rng = random.Random(seed)
    master = build_master(cfg, lot_count, sim_rng)
    res = simulate(cfg, master, lot_count, sim_rng)

    _clear_previous_output(out)
    write_truth(cfg, master, res, out / "truth")

    # separate random stream: changing dirty settings never changes the truth
    dirty_rng = random.Random(seed + 1_000_003)
    dirtier = Dirtier(cfg, master.machines, dirty_rng)
    files_dir = out / "files"
    files_dir.mkdir(parents=True)

    manifest: list[dict[str, Any]] = []
    op_counter: Counter[str] = Counter()
    for doc in render_all(cfg, res):
        is_dirty = dirty_rng.random() >= cfg.dirty["files"]["clean_ratio"]
        injections = dirtier.apply(doc) if is_dirty else []
        op_counter.update(i.op for i in injections)
        path = files_dir / doc.filename
        write_doc(doc, path)
        answers = build_answers(doc, injections, is_dirty)
        _write_answers(path, answers)
        manifest.append(_manifest_row(answers))

        if dirty_rng.random() < cfg.dirty["files"]["duplicate_ratio"]:
            dup = path.with_name(f"{path.stem}_重傳{path.suffix}")
            shutil.copyfile(path, dup)  # byte-identical: same SHA-256
            dup_answers = {
                **answers,
                "file": dup.name,
                "expected_outcome": "DUPLICATE_FILE",
                "duplicate_of": path.name,
                "expected_record_count": 0,
            }
            _write_answers(dup, dup_answers)
            manifest.append(_manifest_row(dup_answers))
            op_counter["F_DUP_FILE"] += 1

    with (out / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)

    summary = {
        "scale": scale,
        "seed": seed,
        "lots": len(res.lots),
        "records": len(res.records),
        "events": dict(Counter(e["event"] for e in res.events)),
        "files": len(manifest),
        "outcomes": dict(Counter(m["expected_outcome"] for m in manifest)),
        "injections": dict(sorted(op_counter.items())),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _write_answers(path: Path, answers: dict[str, Any]) -> None:
    target = path.with_name(f"{path.stem}.answers.json")
    target.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")


def _manifest_row(a: dict[str, Any]) -> dict[str, Any]:
    cats = Counter(i["category"] for i in a["injections"])
    return {
        "file": a["file"],
        "source": a["source"],
        "station": a["station"],
        "is_dirty": a["is_dirty"],
        "expected_outcome": a["expected_outcome"],
        "expected_record_count": a["expected_record_count"],
        "injections_A": cats.get("A", 0),
        "injections_B": cats.get("B", 0),
        "injections_C": cats.get("C", 0),
        "duplicate_of": a.get("duplicate_of", ""),
    }
