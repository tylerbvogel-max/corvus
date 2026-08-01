"""Add neurons.standard_date for regulatory seed compatibility.

Revision ID: 025_standard_date_seed
Revises: 024_cache_coherence
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "025_standard_date_seed"
down_revision = "024_cache_coherence"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    return bool(
        bind.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).first()
    )


def upgrade():
    bind = op.get_bind()
    if not _column_exists(bind, "neurons", "standard_date"):
        op.add_column("neurons", sa.Column("standard_date", sa.String(length=20), nullable=True))


def downgrade():
    bind = op.get_bind()
    if _column_exists(bind, "neurons", "standard_date"):
        op.drop_column("neurons", "standard_date")
