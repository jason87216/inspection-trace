"""Restore the required operator on inspection records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "66f787b931ad"
down_revision: str | Sequence[str] | None = "0b813a49d7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Require an operator, rejecting the migration if any rows lack one."""
    op.alter_column(
        "inspection_record", "operator", existing_type=sa.String(length=20), nullable=False
    )


def downgrade() -> None:
    """Restore the previous nullable schema."""
    op.alter_column(
        "inspection_record", "operator", existing_type=sa.String(length=20), nullable=True
    )
