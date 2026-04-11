"""Enrich neuron_firings with scoring data + add neuron_score_overrides table.

Pattern #2 — Bidirectional lineage / active provenance graph.
Firing records now capture full score breakdown at query time, enabling
forward-chain lineage queries. Score overrides enable manual graph tuning.

Revision ID: 008_enrich_firings_and_score_overrides
Revises: 007_add_actions_table
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "008_firings_overrides"
down_revision: Union[str, None] = "007_add_actions_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enrich neuron_firings with score columns ---
    op.add_column("neuron_firings", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column("neuron_firings", sa.Column("combined_score", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("burst", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("impact", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("precision", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("novelty", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("recency", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("relevance", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("spread_boost", sa.Float(), nullable=True))
    op.add_column("neuron_firings", sa.Column("prompt_position", sa.Integer(), nullable=True))
    op.add_column("neuron_firings", sa.Column("was_included", sa.Boolean(), nullable=True))

    # --- neuron_score_overrides table ---
    op.create_table(
        "neuron_score_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("neuron_id", sa.Integer(), sa.ForeignKey("neurons.id"), nullable=False),
        sa.Column("signal", sa.String(20), nullable=False),
        sa.Column("floor", sa.Float(), nullable=True),
        sa.Column("ceiling", sa.Float(), nullable=True),
        sa.Column("multiplier", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_nso_neuron_id", "neuron_score_overrides", ["neuron_id"])
    op.create_index("ix_nso_neuron_signal", "neuron_score_overrides", ["neuron_id", "signal"], unique=True)


def downgrade() -> None:
    op.drop_table("neuron_score_overrides")
    for col in [
        "rank", "combined_score", "burst", "impact", "precision",
        "novelty", "recency", "relevance", "spread_boost",
        "prompt_position", "was_included",
    ]:
        op.drop_column("neuron_firings", col)
