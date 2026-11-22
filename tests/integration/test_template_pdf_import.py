import csv
from datetime import datetime
from io import StringIO

import httpx2
import pytest
from fastapi.testclient import TestClient

from generator.lab_report import render_lab_pdf, sample_rows
from inspection_trace.api import templates
from inspection_trace.ingestion.errors import (
    E_FILE_FORMAT,
    E_HEADER_UNKNOWN,
    E_NOT_NUMERIC,
    E_UNIT_UNKNOWN,
)

pytestmark = pytest.mark.integration
MAPPING = {
    "lot_no": "Batch",
    "panel": "Piece",
    "point": "Point",
    "machine_code": "Device",
    "item_code": "Metric",
    "value": "Reading",
    "unit": "UOM",
    "time": "Observed",
    "operator": "Operator",
}


def _upload(client: TestClient, name: str, data: bytes, **params: int) -> httpx2.Response:
    return client.post(
        "/imports",
        params={"file_name": name, **params},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_template_requires_approval_before_retrying_an_identical_file(client: TestClient) -> None:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Vendor statement v1"])
    writer.writerow(("Note", *MAPPING.values()))
    for index, row in enumerate(sample_rows()):
        writer.writerow(
            (
                "小計" if index == 0 else "",
                *row[:7],
                datetime.fromisoformat(row[7]).strftime("%d/%m/%Y %H:%M:%S"),
                row[8],
            )
        )
    data = output.getvalue().encode()
    name = "vendor.csv"
    preview = client.post(
        "/templates/preview",
        params={"file_name": name, "header_row": 2},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data_rows"] == 6
    assert preview.json()["suggestions"]["lot_no"] == "Batch"
    rejected = _upload(client, name, data).json()
    assert rejected["errors"][0]["code"] == E_HEADER_UNKNOWN
    body = dict(
        name="供應商報表", file_type="csv", header_row=2, column_map=MAPPING, date_format="dmy"
    )
    created = client.post("/templates", json=body)
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    assert _upload(client, name, data, template_id=template_id).status_code == 409
    template_path = f"/templates/{template_id}"
    edited = client.put(template_path, json={**body, "revision": 1}).json()
    assert edited["revision"] == 2
    assert (
        client.post(
            f"{template_path}/approve", json={"approved_by": "OP", "revision": 1}
        ).status_code
        == 409
    )
    approved = client.post(f"{template_path}/approve", json={"approved_by": "OP", "revision": 2})
    assert approved.json()["status"] == "approved", approved.text
    assert client.put(template_path, json={**body, "revision": 2}).status_code == 409
    accepted = _upload(client, name, data, template_id=template_id).json()
    assert (accepted["status"], accepted["template_id"], accepted["inserted_count"]) == (
        "ACCEPT",
        template_id,
        6,
    )
    assert accepted["total_rows"] == 6
    repeated = _upload(client, name, data).json()
    assert repeated["duplicate_of_id"] == accepted["id"]


def test_template_model_returns_only_observed_headers_and_creates_no_template(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx2.Client

    def reply(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"lot_no":"Batch","panel":"Missing","value":"Reading"}'
                        }
                    }
                ]
            },
        )

    def mock_client(**kwargs: object) -> httpx2.Client:
        return real_client(transport=httpx2.MockTransport(reply))

    monkeypatch.setenv("INSPECTION_TEMPLATE_MODEL_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("INSPECTION_TEMPLATE_MODEL", "fixture")
    monkeypatch.setattr(templates.httpx2, "Client", mock_client)
    suggested = client.post("/templates/suggest", json={"headers": ["Batch", "Reading"]})
    assert suggested.status_code == 200, suggested.text
    assert suggested.json() == {"lot_no": "Batch", "value": "Reading"}
    assert client.get("/templates").json() == []


def test_template_rejects_incompatible_default_unit_without_a_source_column(
    client: TestClient,
) -> None:
    mapping = {field: header for field, header in MAPPING.items() if field != "unit"}
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(mapping.values())
    for row in sample_rows():
        writer.writerow((*row[:6], *row[7:]))
    created = client.post(
        "/templates",
        json=dict(name="預設單位", file_type="csv", column_map=mapping, default_unit="pcs"),
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    approved = client.post(
        f"/templates/{template_id}/approve", json={"approved_by": "OP", "revision": 1}
    )
    assert approved.status_code == 200, approved.text

    response = _upload(client, "vendor.csv", output.getvalue().encode(), template_id=template_id)

    assert response.status_code == 201, response.text
    rejected = response.json()
    assert (rejected["status"], rejected["total_rows"], rejected["inserted_count"]) == (
        "REJECT",
        6,
        0,
    )
    assert [(error["row_no"], error["column"], error["code"]) for error in rejected["errors"]] == [
        (row_no, None, E_UNIT_UNKNOWN) for row_no in range(2, 8)
    ]
    assert all(
        error["raw_value"] is None and "預設單位" in error["message"] and "pcs" in error["message"]
        for error in rejected["errors"]
    )


def test_lab_pdf_uses_existing_import_validation_and_retransmission(client: TestClient) -> None:
    rows = sample_rows()
    bad = list(rows)
    bad[0] = (*bad[0][:5], "invalid", *bad[0][6:])
    rejected = _upload(client, "bad.pdf", render_lab_pdf(bad)).json()
    assert rejected["status"] == "REJECT" and rejected["inserted_count"] == 0
    assert [(row["row_no"], row["column"], row["code"]) for row in rejected["errors"]] == [
        (3, "VALUE", E_NOT_NUMERIC)
    ]
    unsupported = _upload(client, "different.pdf", b"%PDF-1.7\ninvalid").json()
    assert unsupported["errors"][0]["code"] == E_FILE_FORMAT
    data = render_lab_pdf(rows)
    accepted = _upload(client, "lab.pdf", data).json()
    assert (accepted["source_type"], accepted["status"], accepted["inserted_count"]) == (
        "SRC_PDF",
        "ACCEPT",
        6,
    )
    assert _upload(client, "lab-copy.pdf", data).json()["duplicate_of_id"] == accepted["id"]
