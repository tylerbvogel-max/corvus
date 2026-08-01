"""initial schema baseline

Revision ID: ae144af9c1e1
Revises: 
Create Date: 2026-03-21 15:45:49.405373

"""
from typing import Sequence, Union

from alembic import op

from app.models import Base  # noqa: F401  (imports every table into the metadata)

# revision identifiers, used by Alembic.
revision: str = 'ae144af9c1e1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the bootstrap schema for a brand-new database.

    This baseline now owns fresh bootstrap directly so ``alembic upgrade head``
    can initialize an empty database without relying on runtime DDL. It is
    intentionally non-destructive: retired historical tables are preserved
    rather than dropped during upgrade.
    """
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Baseline downgrade is intentionally non-destructive."""
    pass
