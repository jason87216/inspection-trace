"""Export real system output for a static, backend-free demo page.

Runs the same steps as a user: load the sample master data, upload every
sample file through the importer, then read results through the shared
services that back the REST API and MCP. Everything happens in a throwaway
PostgreSQL schema that is rolled back at the end, like evaluate_detection.py,
so existing data is untouched.

    uv run python -m scripts.export_demo --out out/inspection-trace-demo.json
"""

import argparse
import csv
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import load_workbook
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from inspection_trace.db import Base, get_engine
from inspection_trace.ingestion.loader import import_file
from inspection_trace.models import ImportBatch, InspectionItem, InspectionRecord
from inspection_trace.seed import apply_seed, read_seed_tables
from inspection_trace.services import imports, lots, spc
from inspection_trace.services.errors import QueryError

FORMAT_VERSION = 1
TAIPEI = timezone(timedelta(hours=8))
PREVIEW_ROWS = 12

# Examples documented in README; the export records an error instead of failing
# if a different sample set does not contain them.
TRACE_LOT = "L261102A001"
TRACE_MATERIAL = "ABF-3619"
QTIME_PERIOD = (datetime(2026, 11, 2, tzinfo=TAIPEI), datetime(2026, 11, 5, tzinfo=TAIPEI))
README_CHART = ("CORE_LW", "LIN-01", 5)


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def read_grid(path: Path, encoding: str | None) -> list[list[str]]:
    """Return the file as rows of text, row n at index n - 1 (no database needed)."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding=encoding or "utf-8", newline="") as stream:
            return [[_cell(value) for value in row] for row in csv.reader(stream)]
    sheet = load_workbook(path, read_only=True, data_only=True).active
    if sheet is None:
        return []
    return [[_cell(value) for value in row] for row in sheet.iter_rows(values_only=True)]


def build_preview(grid: list[list[str]], error_rows: set[int]) -> list[dict[str, Any]]:
    """First rows of the file plus every row with an error, keeping file row numbers."""
    keep = set(range(1, min(len(grid), PREVIEW_ROWS) + 1)) | {
        row for row in error_rows if 1 <= row <= len(grid)
    }
    width = max((len(row) for row in grid), default=0)
    return [
        {"row": row, "cells": grid[row - 1] + [""] * (width - len(grid[row - 1]))}
        for row in sorted(keep)
    ]


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _query(action: Callable[[], BaseModel]) -> dict[str, Any]:
    try:
        return {"ok": True, "data": _dump(action())}
    except QueryError as error:
        return {"ok": False, "error": error.detail}


def _commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def export(session: Session, samples: Path) -> dict[str, Any]:
    truth = samples / "truth"
    apply_seed(session, read_seed_tables(truth))
    with (samples / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        cases = list(csv.DictReader(stream))

    uploads = []
    for case in cases:
        name = case["file"]
        if Path(name).name != name:
            raise ValueError("Manifest entries must be simple file names.")
        path = samples / "files" / name
        answers = json.loads(path.with_suffix(".answers.json").read_text(encoding="utf-8"))
        batch_id = import_file(
            session, name, path.read_bytes(), operators_path=truth / "operators.csv"
        )
        with session.begin():
            detail = imports.get_import(batch_id, session)
        error_rows = {error.row_no for error in detail.errors if error.row_no is not None}
        uploads.append(
            {
                "file": name,
                "source": case["source"],
                "station": case["station"],
                "expectedOutcome": case["expected_outcome"],
                "expectedRecordCount": int(case["expected_record_count"]),
                "headerRow": answers.get("header_row"),
                "result": _dump(detail),
                "preview": build_preview(read_grid(path, answers.get("encoding")), error_rows),
            }
        )

    with session.begin():
        lot_detail = _query(lambda: lots.lot_detail(TRACE_LOT, session))
        trace_up = _query(lambda: lots.lot_trace(TRACE_LOT, session, "up"))
        trace_down = _query(lambda: lots.lot_trace(TRACE_LOT, session, "down"))
        material_down = _query(lambda: lots.lot_trace(TRACE_MATERIAL, session, "down"))
        qtime = _query(lambda: lots.period_qtime(session, *QTIME_PERIOD))

        item, machine, baseline = README_CHART
        charts = [_query(lambda: spc.chart(session, item, machine, baseline_points=baseline))]
        # Also chart the longest continuous series that was actually imported.
        longest = session.execute(
            select(InspectionRecord.item_code, InspectionRecord.machine_code, func.count())
            .join(InspectionItem, InspectionItem.code == InspectionRecord.item_code)
            .where(InspectionItem.kind == "continuous")
            .group_by(InspectionRecord.item_code, InspectionRecord.machine_code)
            .order_by(func.count().desc(), InspectionRecord.item_code)
            .limit(1)
        ).first()
        if longest is not None and (longest[0], longest[1]) != (item, machine):
            points = min(20, max(5, longest[2] // 3))
            charts.append(
                _query(lambda: spc.chart(session, longest[0], longest[1], baseline_points=points))
            )
        stored = session.scalar(select(func.count(InspectionRecord.id))) or 0
        batches = session.scalar(select(func.count(ImportBatch.id))) or 0

    return {
        "formatVersion": FORMAT_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "commit": _commit(),
        "summary": {
            "files": len(uploads),
            "batches": batches,
            "storedRecords": stored,
            "matchedOutcome": sum(
                upload["result"]["status"] == upload["expectedOutcome"] for upload in uploads
            ),
        },
        "uploads": uploads,
        "lot": {"lotNo": TRACE_LOT, "detail": lot_detail, "up": trace_up, "down": trace_down},
        "material": {"lotNo": TRACE_MATERIAL, "down": material_down},
        "qtime": {
            "from": QTIME_PERIOD[0].isoformat(),
            "to": QTIME_PERIOD[1].isoformat(),
            "result": qtime,
        },
        "charts": charts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path("samples"))
    parser.add_argument("--out", type=Path, default=Path("out/inspection-trace-demo.json"))
    args = parser.parse_args()
    schema = f"demo_{uuid4().hex}"
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(CreateSchema(schema))
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            Base.metadata.create_all(connection)
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                data = export(session, args.samples)
        finally:
            transaction.rollback()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    summary = data["summary"]
    print(
        f"{args.out}: {summary['files']} files, {summary['matchedOutcome']} as expected, "
        f"{summary['storedRecords']} records stored"
    )
    return 0 if summary["matchedOutcome"] == summary["files"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
