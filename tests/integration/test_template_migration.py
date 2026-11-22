"""Exercise a template downgrade with import history and existing measurements."""

from datetime import datetime
from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.orm import Session

from generator.lab_report import sample_rows
from inspection_trace.models import ImportBatch, ImportErrorDetail, ImportTemplate, InspectionRecord

pytestmark = pytest.mark.integration


def test_template_downgrade_keeps_attempts_and_measurement_sources(seeded_session: Session) -> None:
    migration = import_module("migrations.versions.755d1d8eb168_add_import_templates")
    with seeded_session.begin():
        template = ImportTemplate(
            name="Vendor CSV",
            file_type="csv",
            header_row=1,
            column_map={},
            date_format="auto",
            status="draft",
        )
        seeded_session.add(template)
        seeded_session.flush()
        template_id = template.id

        def attempt(status: str, rules: int | None, original_id: int | None = None) -> int:
            row = ImportBatch(
                file_name="vendor.csv",
                file_sha256="a" * 64,
                source_type="SRC_TEMPLATE",
                status=status,
                total_rows=1,
                error_rows=int(status == "REJECT"),
                duplicate_of_id=original_id,
                template_id=rules,
            )
            seeded_session.add(row)
            seeded_session.flush()
            return row.id

        rejected = attempt("REJECT", None)
        repeated_rejection = attempt("DUPLICATE_FILE", None, rejected)
        accepted = attempt("ACCEPT", template_id)
        repeated_success = attempt("DUPLICATE_FILE", template_id, accepted)
        seeded_session.add(
            ImportErrorDetail(
                import_batch_id=rejected,
                code="E_HEADER_UNKNOWN",
                message="Header rejected",
            )
        )
        lot, panel, point, machine, item, value, _, measured_at, operator = sample_rows()[0]
        seeded_session.add(
            InspectionRecord(
                lot_no=lot,
                station_code="C40",
                item_code=item,
                panel=int(panel),
                point=int(point),
                machine_code=machine,
                value=float(value),
                measured_at=datetime.fromisoformat(measured_at),
                operator=operator,
                import_batch_id=accepted,
                source_row_no=2,
            )
        )
        seeded_session.flush()

        connection = seeded_session.connection()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            rows = {
                row.id: row
                for row in connection.execute(
                    text(
                        "SELECT id, status, duplicate_of_id, template_id, warnings "
                        "FROM import_batch"
                    )
                )
            }
            assert set(rows) == {rejected, repeated_rejection, accepted, repeated_success}
            assert rows[accepted].status == "ACCEPT" and rows[accepted].duplicate_of_id is None
            assert all(
                rows[row_id].status == "DUPLICATE_FILE" and rows[row_id].duplicate_of_id == accepted
                for row_id in (rejected, repeated_rejection, repeated_success)
            )
            assert any("REJECT" in warning for warning in rows[rejected].warnings)
            assert f"Template ID before rollback was {template_id}" in rows[accepted].warnings
            assert all(row.template_id is None for row in rows.values())
            assert (
                connection.scalar(text("SELECT import_batch_id FROM inspection_record")) == accepted
            )
            assert connection.scalar(text("SELECT import_batch_id FROM import_error")) == rejected
            migration.upgrade()
