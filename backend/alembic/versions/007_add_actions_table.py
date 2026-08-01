"""Add actions table — universal write primitive (AIP governance pattern #1).

Generalizes the AutopilotProposal -> apply state machine into a typed,
validated, audited record that all mutations pass through. See
~/.claude/plans/staged-booping-globe.md and ROADMAP-WORKLOG.md.

Revision ID: 007_add_actions_table
Revises: 006_add_tiered_edges
Create Date: 2026-04-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '007_add_actions_table'
down_revision: Union[str, Sequence[str], None] = '006_add_tiered_edges'
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
    if _table_exists("actions"):
        return
    op.create_table(
        "actions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=True),
        sa.Column("input_json", JSONB, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "source_query_id", sa.Integer,
            sa.ForeignKey("queries.id"), nullable=True,
        ),
        sa.Column(
            "source_proposal_id", sa.Integer,
            sa.ForeignKey("autopilot_proposals.id"), nullable=True,
        ),
        sa.Column(
            "parent_action_id", sa.Integer,
            sa.ForeignKey("actions.id"), nullable=True,
        ),
        sa.Column(
            "requires_approval", sa.Boolean, nullable=False, server_default="false",
        ),
        sa.Column(
            "state", sa.String(20), nullable=False, server_default="pending",
        ),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime, nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column("result_json", JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False,
        ),
    )

    op.create_index("ix_actions_kind", "actions", ["kind"])
    op.create_index("ix_actions_state", "actions", ["state"])
    op.create_index("ix_actions_source_query_id", "actions", ["source_query_id"])
    op.create_index(
        "ix_actions_source_proposal_id", "actions", ["source_proposal_id"],
    )
    op.create_index("ix_actions_parent_action_id", "actions", ["parent_action_id"])
    op.create_index(
        "ix_actions_idempotency_key", "actions", ["idempotency_key"], unique=True,
    )


def downgrade() -> None:
    if _table_exists("actions"):
        op.drop_index("ix_actions_idempotency_key", table_name="actions")
        op.drop_index("ix_actions_parent_action_id", table_name="actions")
        op.drop_index("ix_actions_source_proposal_id", table_name="actions")
        op.drop_index("ix_actions_source_query_id", table_name="actions")
        op.drop_index("ix_actions_state", table_name="actions")
        op.drop_index("ix_actions_kind", table_name="actions")
        op.drop_table("actions")
