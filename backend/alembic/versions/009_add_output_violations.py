"""Add output_violations table for runtime policy-gate records.

Pattern #7 — Runtime output policy gates (Phase 1.5 GTM gate).
Captures policy violations flagged at LLM-output generation time. Distinct
from `output_checks` (eval-time, advisory). Severity + action drive whether
the response is blocked, redacted, or just flagged.

Revision ID: 009_output_violations
Revises: 008_firings_overrides
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "009_output_violations"
down_revision: Union[str, None] = "008_firings_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Check if a table already exists (handles create_all pre-creation)."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
        {"t": table_name},
    )
    return result.scalar() is not None


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": index_name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _table_exists("output_violations"):
        op.create_table(
            "output_violations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "query_id", sa.Integer(), sa.ForeignKey("queries.id"),
                nullable=True,
            ),
            sa.Column("rule_id", sa.String(80), nullable=False),
            sa.Column("severity", sa.String(20), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("matched_span", sa.Text(), nullable=True),
            sa.Column("redaction", sa.Text(), nullable=True),
            sa.Column("detail", postgresql.JSONB(), nullable=True),
            sa.Column(
                "action_id", sa.Integer(), sa.ForeignKey("actions.id"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    for idx_name, cols in (
        ("ix_output_violations_query_id", ["query_id"]),
        ("ix_output_violations_rule_id", ["rule_id"]),
        ("ix_output_violations_action_id", ["action_id"]),
        ("ix_output_violations_query_severity", ["query_id", "severity"]),
    ):
        if not _index_exists(idx_name):
            op.create_index(idx_name, "output_violations", cols)


def downgrade() -> None:
    for idx_name in (
        "ix_output_violations_query_severity",
        "ix_output_violations_action_id",
        "ix_output_violations_rule_id",
        "ix_output_violations_query_id",
    ):
        if _index_exists(idx_name):
            op.drop_index(idx_name, table_name="output_violations")
    if _table_exists("output_violations"):
        op.drop_table("output_violations")
