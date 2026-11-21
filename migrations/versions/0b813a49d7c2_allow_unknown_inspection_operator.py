"""Allow inspection records whose source did not report an operator."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0b813a49d7c2"
down_revision: str | Sequence[str] | None = "b2747a8cd531"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Accept unknown operators while keeping existing values."""
    op.alter_column(
        "inspection_record", "operator", existing_type=sa.String(length=20), nullable=True
    )


def downgrade() -> None:
    """Restore the requirement. PostgreSQL rejects this if any operators are unknown."""
    op.alter_column(
        "inspection_record", "operator", existing_type=sa.String(length=20), nullable=False
    )
