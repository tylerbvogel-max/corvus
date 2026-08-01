"""roadmap_ledgers — project-scoped long-horizon execution registers.

Revision ID: 022_roadmap_ledgers
Revises: 021_mind_pair_verdicts
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "022_roadmap_ledgers"
down_revision: Union[str, None] = "021_mind_pair_verdicts"
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


def upgrade() -> None:
    if not _table_exists("roadmap_ledgers"):
        op.create_table(
            "roadmap_ledgers",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("slug", sa.String(length=120), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("project_path", sa.String(length=500), nullable=True),
            sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("slug"),
        )
        op.create_index("ix_roadmap_ledgers_slug", "roadmap_ledgers", ["slug"])
        op.create_index("ix_roadmap_ledgers_updated_at", "roadmap_ledgers", ["updated_at"])


def downgrade() -> None:
    if _table_exists("roadmap_ledgers"):
        op.drop_index("ix_roadmap_ledgers_updated_at", table_name="roadmap_ledgers")
        op.drop_index("ix_roadmap_ledgers_slug", table_name="roadmap_ledgers")
        op.drop_table("roadmap_ledgers")
