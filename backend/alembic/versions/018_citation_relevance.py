"""queries.citation_relevance_json — layer-2 citation grounding calibration.

Per-claim embedding-cosine relevance scores for the primary answer's
citations (advisory/log-only). Persisted so the flagging threshold can be
chosen from the real score distribution instead of guessed.

Revision ID: 018_citation_relevance
Revises: 017_drop_corvus_tables
Create Date: 2026-07-08 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "018_citation_relevance"
down_revision: Union[str, None] = "017_drop_corvus_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists("queries", "citation_relevance_json"):
        op.add_column(
            "queries",
            sa.Column("citation_relevance_json", JSONB, nullable=True),
        )


def downgrade() -> None:
    if _column_exists("queries", "citation_relevance_json"):
        op.drop_column("queries", "citation_relevance_json")
