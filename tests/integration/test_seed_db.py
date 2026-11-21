"""Run the seed against a transaction-local PostgreSQL schema."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from inspection_trace.db import Base
from inspection_trace.ingestion.errors import E_SEED_CONFLICT, E_SEED_STALE
from inspection_trace.models import InspectionItem, LotLink, QTimeRule, Station
from inspection_trace.seed import SeedError, apply_seed, plan_seed, read_seed_tables

pytestmark = pytest.mark.integration
TRUTH_DIR = Path(__file__).resolve().parents[2] / "samples" / "truth"


@pytest.fixture
def seed_session(db_session: Session) -> Session:
    connection = db_session.connection()
    schema = f"seed_test_{uuid4().hex}"
    connection.execute(CreateSchema(schema))
    connection.execute(text(f"SET LOCAL search_path TO {schema}"))
    Base.metadata.create_all(connection)
    db_session.commit()
    return db_session


def test_seed_preview_and_repeat_apply(seed_session: Session) -> None:
    tables = read_seed_tables(TRUTH_DIR)
    with seed_session.begin():
        preview = plan_seed(seed_session, tables)
        assert {entry.status for entry in preview} == {"new"}
        assert seed_session.get(Station, "C10") is None

    apply_seed(seed_session, tables, expected=preview)
    apply_seed(seed_session, tables)
    with seed_session.begin():
        assert {entry.status for entry in plan_seed(seed_session, tables)} == {"unchanged"}
        assert seed_session.get(Station, "C10").name == "裁板"
        rule = seed_session.scalar(select(QTimeRule))
        assert (rule.from_station_code, rule.to_station_code, rule.limit_hours) == (
            "B20",
            "B60",
            8.0,
        )
        assert set(
            seed_session.scalars(
                select(LotLink.parent_lot_no).where(LotLink.child_lot_no == "L261102A001")
            )
        ) == {"ABF-3619", "CORE-5456"}


def test_seed_updates_display_fields_preserving_active_and_database_only_rows(
    seed_session: Session,
) -> None:
    tables = read_seed_tables(TRUTH_DIR)
    apply_seed(seed_session, tables)
    with seed_session.begin():
        station = seed_session.get(Station, "C10")
        station.name = "待更新"
        station.seq = 99
        station.active = False
        seed_session.add(Station(code="EXTRA", name="測試站", seq=100))

    apply_seed(seed_session, tables)
    with seed_session.begin():
        station = seed_session.get(Station, "C10")
        assert (station.name, station.seq, station.active) == ("裁板", 1, False)
        assert seed_session.get(Station, "EXTRA") is not None


def test_seed_rejects_a_stale_preview(seed_session: Session) -> None:
    tables = read_seed_tables(TRUTH_DIR)
    with seed_session.begin():
        preview = plan_seed(seed_session, tables)
        seed_session.add(Station(code="C10", name="較新的站名", seq=1))

    with pytest.raises(SeedError) as exc:
        apply_seed(seed_session, tables, expected=preview)
    assert exc.value.code == E_SEED_STALE
    with seed_session.begin():
        assert seed_session.get(Station, "C10").name == "較新的站名"


def test_seed_blocks_unit_conflict_before_writing_other_rows(seed_session: Session) -> None:
    tables = read_seed_tables(TRUTH_DIR)
    apply_seed(seed_session, tables)
    with seed_session.begin():
        seed_session.get(InspectionItem, "CORE_LW").unit = "mm"
        seed_session.get(Station, "C10").name = "待更新"

    with seed_session.begin():
        preview = plan_seed(seed_session, tables)
        blocked = [entry for entry in preview if entry.status == "blocked"]
        assert [(entry.table, entry.key) for entry in blocked] == [
            ("inspection_item", ("CORE_LW",))
        ]
    with pytest.raises(SeedError) as exc:
        apply_seed(seed_session, tables)
    assert exc.value.code == E_SEED_CONFLICT
    with seed_session.begin():
        assert seed_session.get(Station, "C10").name == "待更新"
        assert seed_session.get(InspectionItem, "CORE_LW").unit == "mm"


def test_seed_rolls_back_earlier_inserts_on_database_failure(seed_session: Session) -> None:
    tables = read_seed_tables(TRUTH_DIR)
    with seed_session.begin():
        seed_session.execute(text("ALTER TABLE lot_move ADD CHECK (false)"))

    with pytest.raises(IntegrityError):
        apply_seed(seed_session, tables)
    with seed_session.begin():
        assert seed_session.get(Station, "C10") is None
