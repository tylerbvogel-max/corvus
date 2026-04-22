"""Add autopilot_runs.stage_telemetry_json for Pattern #8.

Pattern #8 — Typed pipeline DAG applied to the autopilot tick.

Mirrors the `queries.stage_telemetry_json` column shipped with Pattern #5.
Each autopilot tick composes a chain of Stage classes through the shared
`run_pipeline` runner, and the resulting per-stage timing + status snapshot
is persisted on the `autopilot_runs` row for post-hoc inspection of the
gap-detection → proposal-curation sequence. JSONB so the shape can evolve
as stages are added or reordered without schema churn.

Revision ID: 012_autopilot_stage_telemetry
Revises: 011_stage_telemetry
Create Date: 2026-04-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "012_autopilot_stage_telemetry"
down_revision: Union[str, None] = "011_stage_telemetry"
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
    if not _column_exists("autopilot_runs", "stage_telemetry_json"):
        op.add_column(
            "autopilot_runs",
            sa.Column(
                "stage_telemetry_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if _column_exists("autopilot_runs", "stage_telemetry_json"):
        op.drop_column("autopilot_runs", "stage_telemetry_json")
