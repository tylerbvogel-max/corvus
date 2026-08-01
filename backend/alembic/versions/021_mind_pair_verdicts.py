"""mind_pair_verdicts — persistent dedup-judge verdicts (graph lint).

Consolidation pair judgments used to evaporate with the HTTP response,
so the same unjudged borderline pairs were recomputed and re-dropped
every janitor run. Persisting (pair, content hashes, verdict) turns
MAX_JUDGED_PAIRS into a rate limit that drains the backlog instead of
a ceiling that never moves. Additive only.

Revision ID: 021_mind_pair_verdicts
Revises: 020_memory_change_log
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021_mind_pair_verdicts"
down_revision: Union[str, None] = "020_memory_change_log"
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
    if not _table_exists("mind_pair_verdicts"):
        op.create_table(
            "mind_pair_verdicts",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("neuron_a_id", sa.Integer(), sa.ForeignKey("neurons.id"), nullable=False),
            sa.Column("neuron_b_id", sa.Integer(), sa.ForeignKey("neurons.id"), nullable=False),
            sa.Column("content_hash_a", sa.String(length=16), nullable=False),
            sa.Column("content_hash_b", sa.String(length=16), nullable=False),
            sa.Column("sim", sa.Float(), nullable=False),
            sa.Column("verdict", sa.String(length=30), nullable=False),
            sa.Column("detail", sa.String(length=500), nullable=True),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="haiku-judge"),
            sa.Column("judged_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("neuron_a_id", "neuron_b_id", name="uq_mind_pair_verdict"),
        )
        op.create_index("ix_mind_pair_verdicts_neuron_a_id", "mind_pair_verdicts", ["neuron_a_id"])
        op.create_index("ix_mind_pair_verdicts_neuron_b_id", "mind_pair_verdicts", ["neuron_b_id"])


def downgrade() -> None:
    if _table_exists("mind_pair_verdicts"):
        op.drop_index("ix_mind_pair_verdicts_neuron_b_id", table_name="mind_pair_verdicts")
        op.drop_index("ix_mind_pair_verdicts_neuron_a_id", table_name="mind_pair_verdicts")
        op.drop_table("mind_pair_verdicts")
