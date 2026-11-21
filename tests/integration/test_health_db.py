"""Integration test: /health against a real PostgreSQL reachable via DATABASE_URL."""

import pytest
from fastapi.testclient import TestClient

from inspection_trace.main import app

pytestmark = pytest.mark.integration


def test_health_reports_database_ok() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
