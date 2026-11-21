"""Verify imports against the generator's answer files in an isolated schema."""

import csv
import json
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inspection_trace.ingestion.errors import (
    E_MEASUREMENT_CONFLICT,
    E_NOT_NUMERIC,
    E_REF_NOT_FOUND,
    E_REQUIRED_MISSING,
)
from inspection_trace.ingestion.loader import import_file
from inspection_trace.models import ImportBatch, ImportErrorDetail, InspectionRecord

pytestmark = pytest.mark.integration
SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_all_21_samples_match_the_manifest_and_errors(seeded_session: Session) -> None:
    with (SAMPLES / "manifest.csv").open(encoding="utf-8", newline="") as file:
        manifest = list(csv.DictReader(file))
    truth = {}
    with (SAMPLES / "truth" / "records.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            key = (
                row["lot_no"],
                row["station"],
                row["item"],
                int(row["panel"]),
                int(row["point"]) if row["point"] else None,
            )
            truth[key] = (float(row["value"]), row["operator"])

    assert len(manifest) == 21
    for expected in manifest:
        path = SAMPLES / "files" / expected["file"]
        batch_id = import_file(seeded_session, path.name, path.read_bytes())
        with seeded_session.begin():
            batch = seeded_session.get(ImportBatch, batch_id)
            issues = list(
                seeded_session.scalars(
                    select(ImportErrorDetail).where(ImportErrorDetail.import_batch_id == batch_id)
                )
            )
            assert batch.status == expected["expected_outcome"], (
                path.name,
                [(i.row_no, i.column, i.code, i.message) for i in issues[:6]],
            )
            records = list(
                seeded_session.scalars(
                    select(InspectionRecord).where(InspectionRecord.import_batch_id == batch_id)
                )
            )
            assert len(records) == int(expected["expected_record_count"]), path.name
            if batch.status == "REJECT":
                answers = json.loads(path.with_suffix(".answers.json").read_text(encoding="utf-8"))
                expected_issues = {
                    (i["row"], i["column"], i["expected_error_code"])
                    for i in answers["injections"]
                    if i["category"] == "B"
                }
                assert {(i.row_no, i.column, i.code) for i in issues} == expected_issues, path.name
            if batch.duplicate_of_id is not None:
                assert (
                    seeded_session.get(ImportBatch, batch.duplicate_of_id).file_name
                    == expected["duplicate_of"]
                )
            for record in records:
                key = (
                    record.lot_no,
                    record.station_code,
                    record.item_code,
                    record.panel,
                    record.point,
                )
                value, operator = truth[key]
                assert record.value == pytest.approx(value, abs=0.001)
                assert record.operator == operator


def test_changed_measurement_rejects_the_entire_file(seeded_session: Session) -> None:
    path = SAMPLES / "files" / "MES_OLD_C40_1151103.csv"
    original_id = import_file(seeded_session, path.name, path.read_bytes())
    with seeded_session.begin():
        original = seeded_session.scalar(
            select(InspectionRecord).where(
                InspectionRecord.import_batch_id == original_id,
                InspectionRecord.source_row_no == 2,
            )
        )
        original_value = original.value
        original_record_id = original.id
    rows = list(csv.reader(path.read_text(encoding="cp950").splitlines()))
    rows[1][5] = str(original_value + 1)
    rows[2][1] = "99"
    changed = "\n".join(",".join(row) for row in rows).encode("cp950")
    changed_id = import_file(seeded_session, "changed.csv", changed)

    with seeded_session.begin():
        assert seeded_session.get(ImportBatch, changed_id).status == "REJECT"
        error = seeded_session.scalar(
            select(ImportErrorDetail).where(ImportErrorDetail.import_batch_id == changed_id)
        )
        assert (error.row_no, error.column, error.code) == (2, "VAL", E_MEASUREMENT_CONFLICT)
        assert seeded_session.get(InspectionRecord, original_record_id).value == original_value
        assert seeded_session.scalar(select(func.count()).select_from(InspectionRecord)) == 6


def test_p3_api_upload_history_report_and_record_filters(client: TestClient) -> None:
    def upload(name: str) -> dict[str, object]:
        response = client.post(
            "/imports",
            params={"file_name": name},
            headers={"Content-Type": "application/octet-stream"},
            content=(SAMPLES / "files" / name).read_bytes(),
        )
        assert response.status_code == 201, response.text
        return response.json()

    good = upload("MES_OLD_C40_1151103.csv")
    assert (good["status"], good["inserted_count"]) == ("ACCEPT", 6)
    bad = upload("MES_OLD_C40_1151102.csv")
    assert (bad["status"], bad["inserted_count"], bad["error_rows"]) == ("REJECT", 0, 2)
    duplicate = upload("MES_OLD_C40_1151103.csv")
    assert (duplicate["status"], duplicate["duplicate_of_id"]) == ("DUPLICATE_FILE", good["id"])

    history = client.get("/imports", params={"status": "REJECT", "page_size": 1}).json()
    assert (history["total"], history["items"][0]["id"]) == (1, bad["id"])
    detail = client.get(f"/imports/{bad['id']}").json()
    assert [issue["row_no"] for issue in detail["errors"]] == [41, 78]
    report = client.get(f"/imports/{bad['id']}/error-report")
    assert report.status_code == 200
    workbook = load_workbook(BytesIO(report.content))
    try:
        assert workbook.active["E3"].value == "量不到"
    finally:
        workbook.close()

    params = {"station_code": "C40", "item_code": "CORE_LW", "page_size": 2}
    first_page = client.get("/records", params=params).json()
    second_page = client.get("/records", params={**params, "page": 2}).json()
    assert (first_page["total"], len(first_page["items"])) == (6, 2)
    assert {row["id"] for row in first_page["items"]}.isdisjoint(
        row["id"] for row in second_page["items"]
    )
    assert first_page["items"][0]["file_name"] == "MES_OLD_C40_1151103.csv"
    assert client.get("/records").status_code == 422
    assert client.get("/records", params={"from": "2026-11-01T00:00:00"}).status_code == 422


def test_upload_rejects_unsupported_content_and_unsafe_name(client: TestClient) -> None:
    response = client.post("/imports", params={"file_name": "sample.csv"}, content=b"file")
    assert response.status_code == 415
    response = client.post(
        "/imports",
        params={"file_name": "../sample.csv"},
        headers={"Content-Type": "application/octet-stream"},
        content=b"file",
    )
    assert response.status_code == 422


def test_c40_decisions_hold_even_when_input_columns_are_reordered(client: TestClient) -> None:
    clean = SAMPLES / "files" / "MES_OLD_C40_1151103.csv"
    rows = list(csv.reader(clean.read_text(encoding="cp950").splitlines()))
    rows[1][0], rows[1][5] = "bad-lot", ""
    rows[2][0], rows[2][5] = "", ""
    rows[3][5] = "49,8"
    rows[4][9] = "OP-9999"
    rendered = StringIO(newline="")
    csv.writer(rendered).writerows(
        [*reversed(row), "SHIFT" if number == 0 else "日班"] for number, row in enumerate(rows)
    )
    response = client.post(
        "/imports",
        params={"file_name": "reordered.csv"},
        headers={"Content-Type": "application/octet-stream"},
        content=rendered.getvalue().encode("utf-8"),
    )
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "REJECT"
    assert result["warnings"] == ["第 11 欄 'SHIFT' 未使用"]
    assert [(issue["row_no"], issue["column"], issue["code"]) for issue in result["errors"]] == [
        (2, "VAL", E_REQUIRED_MISSING),
        (3, "LOTNO", E_REQUIRED_MISSING),
        (4, "VAL", E_NOT_NUMERIC),
        (5, "OP", E_REF_NOT_FOUND),
    ]
