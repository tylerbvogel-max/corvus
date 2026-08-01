"""Delivery-pathway habituation ledger (mind-delivery-plasticity).

Revision ID: 026_delivery_pathways
Revises: 025_standard_date_seed
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "026_delivery_pathways"
down_revision = "025_standard_date_seed"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).first()
    )


def upgrade():
    bind = op.get_bind()
    if _table_exists(bind, "delivery_pathways"):
        return
    op.create_table(
        "delivery_pathways",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("neuron_id", sa.Integer(), sa.ForeignKey("neurons.id"), nullable=False),
        sa.Column("trigger", sa.String(length=60), nullable=False),
        sa.Column("tool", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("delivered_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rewarded_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("penalized_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_delivered_at", sa.DateTime(), nullable=True),
        sa.Column("last_rewarded_at", sa.DateTime(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("state_changed_at", sa.DateTime(), nullable=True),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("autopilot_proposals.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("neuron_id", "trigger", "tool", name="uq_delivery_pathway"),
    )
    op.create_index("ix_delivery_pathways_neuron_id", "delivery_pathways", ["neuron_id"])


def downgrade():
    bind = op.get_bind()
    if _table_exists(bind, "delivery_pathways"):
        op.drop_index("ix_delivery_pathways_neuron_id", table_name="delivery_pathways")
        op.drop_table("delivery_pathways")
