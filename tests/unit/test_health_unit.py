"""Unit test for /health without a database: the session is replaced by a fake."""

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from inspection_trace.db import get_session
from inspection_trace.main import app


class UnreachableDatabaseSession:
    """Stands in for a Session whose database cannot be reached."""

    def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def unreachable_session() -> Iterator[UnreachableDatabaseSession]:
    yield UnreachableDatabaseSession()


def test_health_returns_503_when_database_is_unreachable() -> None:
    app.dependency_overrides[get_session] = unreachable_session
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
