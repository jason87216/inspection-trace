"""initial empty

Revision ID: ba3e864a78da
Revises:
Create Date: 2026-11-20 13:38:52.168128

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "ba3e864a78da"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
