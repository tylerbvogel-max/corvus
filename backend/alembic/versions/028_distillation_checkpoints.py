"""Durable distillation input boundaries and pending enrichment.

The baseline creates current metadata, so fresh installs may have this table.
No historical filesystem markers are imported or reinterpreted.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "028_distillation_checkpoints"
down_revision = "027_synaptic_homeostasis"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("distillation_checkpoints"):
        op.create_table(
            "distillation_checkpoints",
            sa.Column("source_id", sa.String(64), primary_key=True),
            sa.Column("state", JSONB(), nullable=False),
        )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("distillation_checkpoints"):
        op.drop_table("distillation_checkpoints")
