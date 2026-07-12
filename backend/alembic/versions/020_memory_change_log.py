"""memory_change_log — temporal change log for memory rows (kill-temporal-kg).

Janitor supersede/demote/promote paths append (old_value, new_value,
changed_at, reason) per mutated field so historical belief states stay
queryable instead of being overwritten in place.

Revision ID: 020_memory_change_log
Revises: 019_eval_scores_half_steps
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020_memory_change_log"
down_revision: Union[str, None] = "019_eval_scores_half_steps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_change_log",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("neuron_id", sa.Integer(), sa.ForeignKey("neurons.id"), nullable=False),
        sa.Column("field", sa.String(length=50), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("actor", sa.String(length=50), nullable=False, server_default="mind_janitor"),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_change_log_neuron_id", "memory_change_log", ["neuron_id"])
    op.create_index("ix_memory_change_log_changed_at", "memory_change_log", ["changed_at"])


def downgrade() -> None:
    op.drop_index("ix_memory_change_log_changed_at", table_name="memory_change_log")
    op.drop_index("ix_memory_change_log_neuron_id", table_name="memory_change_log")
    op.drop_table("memory_change_log")
