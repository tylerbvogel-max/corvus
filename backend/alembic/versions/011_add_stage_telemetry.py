"""Add queries.stage_telemetry_json for Pattern #5.

Pattern #5 — Typed pipeline DAG with observable stages.

Stores the per-stage timing + status snapshot produced by each query's
prep pipeline (structural_resolve → classify → prefilter_score → …). JSONB
column so the shape can evolve as stages are added/reordered without
schema churn, bounded by ~8 stages × ~100 bytes per row in practice.

Revision ID: 011_stage_telemetry
Revises: 010_eval_runs
Create Date: 2026-04-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "011_stage_telemetry"
down_revision: Union[str, None] = "010_eval_runs"
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
    if not _column_exists("queries", "stage_telemetry_json"):
        op.add_column(
            "queries",
            sa.Column(
                "stage_telemetry_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if _column_exists("queries", "stage_telemetry_json"):
        op.drop_column("queries", "stage_telemetry_json")
