"""The database itself rejects invalid master data and lots (CHECK, UNIQUE, FK constraints).

Assertions compare constraint names, not error messages.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from psycopg.errors import NotNullViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inspection_trace.ingestion.errors import E_FILE_ENCODING
from inspection_trace.models import (
    ImportBatch,
    ImportErrorDetail,
    InspectionItem,
    InspectionRecord,
    Lot,
    LotLink,
    Machine,
    Part,
    Station,
)

pytestmark = pytest.mark.integration

TAIPEI = ZoneInfo("Asia/Taipei")
T0 = datetime(2026, 11, 2, 8, 0, tzinfo=TAIPEI)


def violated_constraint(session: Session, row: object) -> str:
    """Insert one row, expect the database to refuse it, return the violated constraint's name."""
    session.add(row)
    with pytest.raises(IntegrityError) as exc_info:
        session.flush()
    session.rollback()
    return exc_info.value.orig.diag.constraint_name  # type: ignore[union-attr]


def add_part(session: Session) -> None:
    session.add(Part(part_no="T-PART-1", name="test part"))
    session.flush()


def production_lot(lot_no: str = "T-LOT-1", **overrides: object) -> Lot:
    values: dict[str, object] = {
        "lot_no": lot_no,
        "lot_type": "production",
        "part_no": "T-PART-1",
        "panel_count": 20,
        "released_at": T0,
    }
    values.update(overrides)
    return Lot(**values)


def test_valid_production_and_material_lots_are_accepted(db_session: Session) -> None:
    add_part(db_session)
    db_session.add(production_lot())
    db_session.add(Lot(lot_no="T-ABF-1", lot_type="material", kind="ABF"))
    db_session.flush()

    assert db_session.get(Lot, "T-LOT-1") is not None
    assert db_session.get(Lot, "T-ABF-1") is not None


def test_production_lot_without_part_is_rejected(db_session: Session) -> None:
    add_part(db_session)

    assert violated_constraint(db_session, production_lot(part_no=None)) == (
        "ck_lot_production_fields"
    )


def test_material_lot_without_kind_is_rejected(db_session: Session) -> None:
    lot = Lot(lot_no="T-ABF-1", lot_type="material")

    assert violated_constraint(db_session, lot) == "ck_lot_material_fields"


def test_material_lot_with_part_is_rejected(db_session: Session) -> None:
    add_part(db_session)
    lot = Lot(lot_no="T-ABF-1", lot_type="material", kind="ABF", part_no="T-PART-1")

    assert violated_constraint(db_session, lot) == "ck_lot_material_fields"


def test_machine_valid_to_must_be_after_valid_from(db_session: Session) -> None:
    db_session.add(Station(code="T-ST", name="test station", seq=99))
    db_session.flush()
    machine = Machine(code="T-M-1", station_code="T-ST", valid_from=T0, valid_to=T0)

    assert violated_constraint(db_session, machine) == "ck_machine_valid_period"


def test_machine_requires_existing_station(db_session: Session) -> None:
    machine = Machine(code="T-M-1", station_code="NO-SUCH-STATION", valid_from=T0)

    assert violated_constraint(db_session, machine) == "fk_machine_station_code_station"


def test_duplicate_lot_link_is_rejected(db_session: Session) -> None:
    add_part(db_session)
    db_session.add_all([production_lot(), Lot(lot_no="T-ABF-1", lot_type="material", kind="ABF")])
    db_session.add(LotLink(parent_lot_no="T-ABF-1", child_lot_no="T-LOT-1", relation="consume"))
    db_session.flush()
    duplicate = LotLink(parent_lot_no="T-ABF-1", child_lot_no="T-LOT-1", relation="consume")

    assert violated_constraint(db_session, duplicate) == "uq_lot_link_parent_lot_no_child_lot_no"


def test_station_active_defaults_to_true_in_the_database(db_session: Session) -> None:
    station = Station(code="T-ST", name="test station", seq=99)
    db_session.add(station)
    db_session.flush()
    db_session.refresh(station)

    assert station.active is True


def import_batch(file_digest: str) -> ImportBatch:
    return ImportBatch(
        file_name="test.csv",
        file_sha256=file_digest,
        source_type="SRC_MACHINE",
        status="ACCEPT",
        total_rows=1,
        error_rows=0,
    )


def test_file_sha256_cannot_be_reused(db_session: Session) -> None:
    digest = "a" * 64
    db_session.add(import_batch(digest))
    db_session.flush()

    assert violated_constraint(db_session, import_batch(digest)) == "uq_import_batch_file_sha256"


def test_file_level_error_can_be_stored_without_cell_coordinates(db_session: Session) -> None:
    batch = import_batch("d" * 64)
    batch.status = "REJECT"
    batch.total_rows = 0
    db_session.add(batch)
    db_session.flush()
    detail = ImportErrorDetail(
        import_batch_id=batch.id, code=E_FILE_ENCODING, message="Invalid file encoding"
    )
    db_session.add(detail)
    db_session.flush()
    db_session.refresh(detail)

    assert (detail.row_no, detail.column, detail.raw_value) == (None, None, None)


@pytest.fixture
def count_measurement_fields(db_session: Session) -> dict[str, object]:
    add_part(db_session)
    db_session.add_all([Station(code="T-IMPORT", name="test station", seq=99), production_lot()])
    db_session.flush()
    db_session.add_all(
        [
            Machine(code="T-IMPORT-M", station_code="T-IMPORT", valid_from=T0),
            InspectionItem(
                code="T-IMPORT-I",
                name="defects",
                station_code="T-IMPORT",
                kind="count",
                unit="pcs",
                usl=3.0,
                physical_min=0.0,
            ),
        ]
    )
    db_session.flush()
    return {
        "lot_no": "T-LOT-1",
        "station_code": "T-IMPORT",
        "machine_code": "T-IMPORT-M",
        "item_code": "T-IMPORT-I",
        "panel": 1,
        "point": None,
        "value": 1.0,
        "measured_at": T0,
        "operator": "T-OP",
        "source_row_no": 2,
    }


def test_measurement_requires_an_operator(
    db_session: Session, count_measurement_fields: dict[str, object]
) -> None:
    batch = import_batch("e" * 64)
    db_session.add(batch)
    db_session.flush()
    fields = {**count_measurement_fields, "operator": None}
    record = InspectionRecord(**fields, import_batch_id=batch.id)
    db_session.add(record)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.flush()

    error = exc_info.value.orig
    assert isinstance(error, NotNullViolation)
    assert error.diag.column_name == "operator"


def test_count_measurement_with_null_point_cannot_repeat_across_imports(
    db_session: Session, count_measurement_fields: dict[str, object]
) -> None:
    first_batch = import_batch("b" * 64)
    second_batch = import_batch("c" * 64)
    db_session.add_all([first_batch, second_batch])
    db_session.flush()
    fields = count_measurement_fields
    db_session.add(InspectionRecord(**fields, import_batch_id=first_batch.id))
    db_session.flush()

    assert (
        violated_constraint(db_session, InspectionRecord(**fields, import_batch_id=second_batch.id))
        == "uq_inspection_record_measurement"
    )
