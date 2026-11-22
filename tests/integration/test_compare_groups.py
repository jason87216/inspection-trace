from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from inspection_trace.ingestion.loader import import_file
from inspection_trace.services.compare import compare_groups

pytestmark = pytest.mark.integration


def test_compare_groups_uses_measurement_evidence_and_material_relationships(
    seeded_session: Session,
) -> None:
    sample = Path(__file__).resolve().parents[2] / "samples/files/MES_OLD_C40_1151103.csv"
    import_file(seeded_session, sample.name, sample.read_bytes())
    start = datetime(2026, 11, 2, tzinfo=UTC)
    end = datetime(2026, 11, 5, tzinfo=UTC)

    machine = compare_groups(seeded_session, "CORE_LW", start, end, "machine")
    assert [(row.group, row.count, row.out_of_spec_count) for row in machine] == [("LIN-01", 6, 0)]
    assert machine[0].evidence_record_id > 0
    material = compare_groups(seeded_session, "CORE_LW", start, end, "material")
    assert len(material) == 2
    assert all(row.count == 6 and row.mean == pytest.approx(machine[0].mean) for row in material)
    shifts = compare_groups(seeded_session, "CORE_LW", start, end, "shift")
    assert sum(row.count for row in shifts) == 6
