"""Per-region config over the shared substrate (plat-region-config).

- region_policies table: per-silo scoring weights, loop config, ACL,
  write-gate overrides, and ontology projection — all over ONE graph.
- neurons.visibility: per-neuron ACL override (NULL inherits region default).
- autopilot_config.region: NULL = global loop; set = a region's vertical loop.

Revision ID: 014_region_policies
Revises: 013_substrate_ontology
Create Date: 2026-07-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "014_region_policies"
down_revision: Union[str, None] = "013_substrate_ontology"
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
    if not _table_exists("region_policies"):
        op.create_table(
            "region_policies",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("region", sa.String(length=100), nullable=False, unique=True),
            sa.Column("display_name", sa.String(length=200), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("scoring_weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("loop_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("acl", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("write_gate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("projection", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("ix_region_policies_region", "region_policies", ["region"])

    if not _column_exists("neurons", "visibility"):
        op.add_column(
            "neurons", sa.Column("visibility", sa.String(length=20), nullable=True),
        )

    if not _column_exists("autopilot_config", "region"):
        op.add_column(
            "autopilot_config",
            sa.Column("region", sa.String(length=100), nullable=True),
        )
        op.create_index("ix_autopilot_config_region", "autopilot_config", ["region"])


def downgrade() -> None:
    if _column_exists("autopilot_config", "region"):
        op.drop_index("ix_autopilot_config_region", table_name="autopilot_config")
        op.drop_column("autopilot_config", "region")
    if _column_exists("neurons", "visibility"):
        op.drop_column("neurons", "visibility")
    if _table_exists("region_policies"):
        op.drop_index("ix_region_policies_region", table_name="region_policies")
        op.drop_table("region_policies")
