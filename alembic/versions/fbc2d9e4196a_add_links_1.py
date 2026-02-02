"""add links 1

Revision ID: fbc2d9e4196a
Revises: ece05955c9de
Create Date: 2026-01-18 00:03:02.858392

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbc2d9e4196a'
down_revision: Union[str, Sequence[str], None] = 'ece05955c9de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
