"""Measure error detection rate against the generator's answer files (P4)."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from math import isclose
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from inspection_trace.db import Base, get_engine
from inspection_trace.ingestion.loader import ACCEPT, DUPLICATE_FILE, REJECT, import_file
from inspection_trace.ingestion.normalization import TAIPEI
from inspection_trace.models import ImportBatch, ImportErrorDetail, InspectionItem, InspectionRecord
from inspection_trace.seed import apply_seed, read_seed_tables

type IssueKey = tuple[int | None, str | None, str]


class Injection(TypedDict):
    op: str
    category: str
    row: int
    column: str | None
    column_key: str | None
    expected_error_code: str | None
    expected_normalized_value: str | float | None
    tolerance: float | None


@dataclass
class Evaluation:
    files: Counter[str] = field(default_factory=Counter)
    correct_files: Counter[str] = field(default_factory=Counter)
    hits: Counter[str] = field(default_factory=Counter)
    missed: Counter[str] = field(default_factory=Counter)
    false_positives: Counter[str] = field(default_factory=Counter)
    correction_count: int = 0
    correction_hits: int = 0
    failures: list[str] = field(default_factory=list)


def match_errors(
    expected: Iterable[IssueKey], actual: Iterable[IssueKey]
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    """Count matched, missed, and extra errors by code. Each location must match."""
    wanted, found = Counter(expected), Counter(actual)

    def by_code(issues: Counter[IssueKey]) -> Counter[str]:
        count: Counter[str] = Counter()
        for (_, _, code), quantities in issues.items():
            count[code] += quantities
        return count

    return by_code(wanted & found), by_code(wanted - found), by_code(found - wanted)


def _check_correction(
    injection: Injection,
    rows: dict[int, list[InspectionRecord]],
    units: dict[str, str],
) -> bool:
    op = injection["op"]
    expected = injection["expected_normalized_value"]
    records = rows.get(injection["row"], [])
    tolerance = injection["tolerance"] or 0.0
    if op == "D_HEADER_ALIAS":
        # The caller already checked that all expected measurements were stored.
        return True
    if op == "D_LAYOUT_NOISE":
        return not records
    if op in {"D_FULLWIDTH", "D_UNIT_MIL"} and isinstance(expected, (int, float)):
        key = injection["column_key"] or ""
        if key.startswith("P") and key[1:].isdigit():
            records = [record for record in records if record.point == int(key[1:])]
        return bool(records) and all(
            isclose(record.value, expected, rel_tol=0, abs_tol=tolerance) for record in records
        )
    if not records:
        return False
    if op == "D_WHITESPACE":
        return all(record.lot_no == expected for record in records)
    if op == "D_UNIT_ALIAS":
        return all(units[record.item_code] == expected for record in records)
    if op == "D_DATE_FORMAT" and isinstance(expected, str):
        original = datetime.fromisoformat(expected).replace(tzinfo=TAIPEI)
        return all(
            abs((record.measured_at - original).total_seconds()) <= tolerance for record in records
        )
    return False


def evaluate_samples(session: Session, samples: Path) -> Evaluation:
    """Run the actual importer in manifest order and evaluate saved results."""
    with (samples / "manifest.csv").open(encoding="utf-8-sig", newline="") as file:
        cases = list(csv.DictReader(file))
    if not cases:
        raise ValueError("The sample manifest has no cases.")
    truth = samples / "truth"
    apply_seed(session, read_seed_tables(truth))
    with session.begin():
        units = dict(session.execute(select(InspectionItem.code, InspectionItem.unit)).all())

    result = Evaluation()
    for case in cases:
        file_name = case["file"]
        if Path(file_name).name != file_name or any(char in file_name for char in "/\\:"):
            raise ValueError("Manifest entries must be simple file names.")
        path = samples / "files" / file_name
        answers = json.loads(path.with_suffix(".answers.json").read_text(encoding="utf-8"))
        injections: list[Injection] = answers["injections"]
        expected_status = case["expected_outcome"]
        result.files[expected_status] += 1
        batch_id = import_file(
            session, file_name, path.read_bytes(), operators_path=truth / "operators.csv"
        )

        with session.begin():
            batch = session.get_one(ImportBatch, batch_id)
            records = list(
                session.scalars(
                    select(InspectionRecord).where(InspectionRecord.import_batch_id == batch_id)
                )
            )
            if batch.status == expected_status:
                result.correct_files[expected_status] += 1
            else:
                result.failures.append(f"{file_name}: 預期 {expected_status}，實際 {batch.status}")
            expected_count = int(case["expected_record_count"])
            if len(records) != expected_count:
                result.failures.append(
                    f"{file_name}: 預期入庫 {expected_count} 筆，實際 {len(records)} 筆"
                )
            if expected_status == DUPLICATE_FILE:
                original = (
                    session.get(ImportBatch, batch.duplicate_of_id)
                    if batch.duplicate_of_id
                    else None
                )
                if original is None or original.file_name != case["duplicate_of"]:
                    result.failures.append(f"{file_name}: 重傳未對應正確原始批次")
                continue

            expected = [
                (entry["row"], entry["column"], entry["expected_error_code"])
                for entry in injections
                if entry["category"] == "B" and entry["expected_error_code"] is not None
            ]
            actual = [
                (row_no, column, code)
                for row_no, column, code in session.execute(
                    select(
                        ImportErrorDetail.row_no, ImportErrorDetail.column, ImportErrorDetail.code
                    ).where(ImportErrorDetail.import_batch_id == batch_id)
                )
            ]
            hits, missed, extras = match_errors(expected, actual)
            result.hits.update(hits)
            result.missed.update(missed)
            result.false_positives.update(extras)
            if missed or extras:
                result.failures.append(f"{file_name}: 漏抓 {dict(missed)}，誤報 {dict(extras)}")

            if expected_status != ACCEPT:
                continue
            rows: dict[int, list[InspectionRecord]] = defaultdict(list)
            for record in records:
                rows[record.source_row_no].append(record)
            for injection in injections:
                if injection["category"] != "A":
                    continue
                result.correction_count += 1
                if (
                    batch.status == ACCEPT
                    and len(records) == expected_count
                    and _check_correction(injection, rows, units)
                ):
                    result.correction_hits += 1
                else:
                    result.failures.append(
                        f"{file_name}: 第 {injection['row']} 列修正驗證失敗（{injection['op']}）"
                    )
    return result


def render_report(result: Evaluation) -> str:
    """Build a Markdown report suitable for the README or the terminal."""
    lines = [
        "| 檔案判定 | 預期檔數 | 判定正確 |",
        "| --- | ---: | ---: |",
        *[
            f"| {status} | {result.files[status]} | {result.correct_files[status]} |"
            for status in (ACCEPT, REJECT, DUPLICATE_FILE)
        ],
        "",
        "| 錯誤代碼 | 預期 | 命中 | 漏抓 | 誤報 | 召回率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    codes = sorted(result.hits.keys() | result.missed.keys() | result.false_positives.keys())
    for code in codes:
        hits, missed, extra = result.hits[code], result.missed[code], result.false_positives[code]
        total = hits + missed
        recall = f"{hits / total:.0%}" if total else "—"
        lines.append(f"| `{code}` | {total} | {hits} | {missed} | {extra} | {recall} |")
    hits, missed = result.hits.total(), result.missed.total()
    total = hits + missed
    recall = f"{hits / total:.0%}" if total else "—"
    lines.append(
        f"| **合計** | {total} | {hits} | {missed} | {result.false_positives.total()} | {recall} |"
    )
    lines.extend(
        ("", f"A 類修正（預期可入庫的檔案）：{result.correction_hits}/{result.correction_count}。")
    )
    if result.failures:
        lines.extend(("", "差異：", *(f"- {failure}" for failure in result.failures)))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path("samples"))
    args = parser.parse_args()
    schema = f"detection_{uuid4().hex}"
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(CreateSchema(schema))
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            Base.metadata.create_all(connection)
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                result = evaluate_samples(session, args.samples)
        finally:
            transaction.rollback()
    print(render_report(result))
    return int(bool(result.failures))


if __name__ == "__main__":
    raise SystemExit(main())
