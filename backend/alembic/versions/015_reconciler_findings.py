"""Horizontal reconciler routing (plat-reconciler).

integrity_findings.region — the owning silo whose controller resolves a
cross-region finding (contradiction / staleness divergence / homonym-synonym
/ seam gap). NULL for tenant-global findings from the vertical scans.

Revision ID: 015_reconciler_findings
Revises: 014_region_policies
Create Date: 2026-07-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "015_reconciler_findings"
down_revision: Union[str, None] = "014_region_policies"
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
    if not _column_exists("integrity_findings", "region"):
        op.add_column(
            "integrity_findings",
            sa.Column("region", sa.String(length=100), nullable=True),
        )
        op.create_index(
            "ix_integrity_findings_region", "integrity_findings", ["region"],
        )


def downgrade() -> None:
    if _column_exists("integrity_findings", "region"):
        op.drop_index("ix_integrity_findings_region", table_name="integrity_findings")
        op.drop_column("integrity_findings", "region")
