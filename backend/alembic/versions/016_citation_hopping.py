"""Frequency-hopped citation grounding (anti-hallucination exit layer).

citation_hop_sessions — secret per-query {token: neuron_id} maps plus the
verification audit for the frequency-hopping citation layer.
queries.citation_hop_session_id links an internally-executed answer to the
session that graded its citations.

Revision ID: 016_citation_hopping
Revises: 015_reconciler_findings
Create Date: 2026-07-03 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "016_citation_hopping"
down_revision: Union[str, None] = "015_reconciler_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
        ),
        {"t": table_name},
    )
    return result.scalar() is not None


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
    if not _table_exists("citation_hop_sessions"):
        op.create_table(
            "citation_hop_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("token_map_json", JSONB(), nullable=False),
            sa.Column("required_json", JSONB(), nullable=True),
            sa.Column("audit_json", JSONB(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True,
            ),
        )
    if not _column_exists("queries", "citation_hop_session_id"):
        op.add_column(
            "queries",
            sa.Column("citation_hop_session_id", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("queries", "citation_hop_session_id"):
        op.drop_column("queries", "citation_hop_session_id")
    if _table_exists("citation_hop_sessions"):
        op.drop_table("citation_hop_sessions")
