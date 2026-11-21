"""Integration test fixtures: every test runs inside a transaction that is rolled back (P1-13)."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from inspection_trace.db import Base, get_engine, get_session
from inspection_trace.main import app
from inspection_trace.seed import apply_seed, read_seed_tables


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A Session whose changes all disappear when the test ends.

    The outer transaction belongs to the test. With join_transaction_mode="create_savepoint",
    code under test may call session.commit(): that only releases a SAVEPOINT, and the final
    rollback still undoes everything, so the development database is left as it was.
    """
    connection = get_engine().connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def seeded_session(db_session: Session) -> Session:
    """Load the sample masters into a fresh schema rolled back with the test."""
    connection = db_session.connection()
    schema = f"seeded_test_{uuid4().hex}"
    connection.execute(CreateSchema(schema))
    connection.execute(text(f"SET LOCAL search_path TO {schema}"))
    Base.metadata.create_all(connection)
    db_session.commit()
    truth = Path(__file__).resolve().parents[2] / "samples" / "truth"
    apply_seed(db_session, read_seed_tables(truth))
    return db_session


@pytest.fixture
def client(seeded_session: Session) -> Iterator[TestClient]:
    def use_seeded_session() -> Iterator[Session]:
        try:
            yield seeded_session
        finally:
            seeded_session.rollback()

    app.dependency_overrides[get_session] = use_seeded_session
    try:
        with TestClient(app) as browser:
            yield browser
    finally:
        app.dependency_overrides.pop(get_session)
