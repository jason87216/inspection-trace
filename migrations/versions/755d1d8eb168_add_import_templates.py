"""Store approved import rules and allow a rejected file to use a new template."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "755d1d8eb168"
down_revision: str | Sequence[str] | None = "0f72da83da92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_template",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("file_type", sa.String(4), nullable=False),
        sa.Column("header_row", sa.Integer(), nullable=False),
        sa.Column("column_map", sa.JSON(), nullable=False),
        sa.Column("date_format", sa.String(12), nullable=False),
        sa.Column("default_unit", sa.String(10)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("approved_by", sa.String(50)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_template")),
        sa.CheckConstraint(
            "status IN ('draft', 'approved')", name=op.f("ck_import_template_status")
        ),
        sa.CheckConstraint(
            "header_row BETWEEN 1 AND 30", name=op.f("ck_import_template_header_row")
        ),
        sa.CheckConstraint(
            "file_type IN ('csv', 'xlsx')", name=op.f("ck_import_template_file_type")
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND approved_by IS NULL AND approved_at IS NULL)"
            " OR (status = 'approved' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name=op.f("ck_import_template_approval"),
        ),
    )
    op.create_foreign_key(
        op.f("fk_import_batch_template_id_import_template"),
        "import_batch",
        "import_template",
        ["template_id"],
        ["id"],
    )
    op.drop_index("uq_import_batch_file_sha256", table_name="import_batch")
    op.create_index(
        "uq_import_batch_file_sha256",
        "import_batch",
        ["file_sha256", "template_id"],
        unique=True,
        postgresql_where=sa.text("duplicate_of_id IS NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_import_batch_file_sha256", table_name="import_batch")
    # The old schema allows one original per digest. Keep an accepted original when
    # available and collapse links to it without moving measurements or error details.
    op.execute(
        """
        WITH originals AS (
            SELECT id,
                   first_value(id) OVER (
                       PARTITION BY file_sha256 ORDER BY (status = 'ACCEPT') DESC, id
                   ) AS kept_id
            FROM import_batch
            WHERE duplicate_of_id IS NULL
        )
        UPDATE import_batch AS batch
        SET duplicate_of_id = originals.kept_id,
            status = 'DUPLICATE_FILE',
            warnings = CASE WHEN batch.id = originals.id THEN
                (batch.warnings::jsonb
                    || jsonb_build_array('Import status before template rollback was ' || batch.status))::json
                ELSE batch.warnings
            END
        FROM originals
        WHERE originals.id <> originals.kept_id
          AND (batch.id = originals.id OR batch.duplicate_of_id = originals.id)
        """
    )
    op.execute(
        """
        UPDATE import_batch
        SET template_id = NULL,
            warnings = (warnings::jsonb
                || jsonb_build_array('Template ID before rollback was ' || template_id))::json
        WHERE template_id IS NOT NULL
        """
    )
    op.create_index(
        "uq_import_batch_file_sha256",
        "import_batch",
        ["file_sha256"],
        unique=True,
        postgresql_where=sa.text("duplicate_of_id IS NULL"),
    )
    op.drop_constraint(
        op.f("fk_import_batch_template_id_import_template"), "import_batch", type_="foreignkey"
    )
    op.drop_table("import_template")
