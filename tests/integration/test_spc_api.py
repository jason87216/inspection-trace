from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration
SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "files" / "MES_OLD_C40_1151103.csv"


def test_chart_reads_imported_observations_and_reports_when_baseline_is_missing(
    client: TestClient,
) -> None:
    imported = client.post(
        "/imports",
        params={"file_name": SAMPLE.name},
        headers={"Content-Type": "application/octet-stream"},
        content=SAMPLE.read_bytes(),
    )
    assert imported.json()["inserted_count"] == 6

    path = "/spc/CORE_LW"
    response = client.get(path, params={"machine": "LIN-01", "baseline_points": 5})
    assert response.status_code == 200
    result = response.json()
    assert len(result["points"]) == 6
    assert result["individual_limits"]["center"] == pytest.approx(
        sum(point["value"] for point in result["points"][:5]) / 5
    )
    assert result["points"][0]["moving_range"] is None
    assert not any(point["signals"] for point in result["points"][:5])

    short = client.get(path, params={"machine": "LIN-01"}).json()
    assert short["individual_limits"] is None
    assert len(short["points"]) == 6
    empty = client.get(path, params={"machine": "LIN-02"}).json()
    assert empty["points"] == []


def test_chart_rejects_invalid_series_and_filters(client: TestClient) -> None:
    path = "/spc/CORE_LW"
    assert client.get(path).status_code == 422
    assert client.get(path, params={"machine": "MISSING"}).status_code == 404
    assert client.get(path, params={"machine": "LDR-01"}).status_code == 422
    assert client.get(path, params={"machine": "LIN-01", "baseline_points": 4}).status_code == 422
    assert client.get(path, params={"machine": "LIN-01", "from": "2026-11-04"}).status_code == 422
    assert client.get("/spc/CORE_AOI_DEF", params={"machine": "LIN-01"}).status_code == 422
