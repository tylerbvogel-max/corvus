"""Homeostatic axis on neurons (mind-synaptic-downscaling).

Adds the sleep-side scalar. Deliberately separate from avg_utility, which
is evidence and is read by the authority ladder and by recall scoring —
renormalizing that one globally would move neurons across trust tiers as a
side effect.

Revision ID: 027_synaptic_homeostasis
Revises: 026_delivery_pathways
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "027_synaptic_homeostasis"
down_revision = "026_delivery_pathways"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    return bool(
        bind.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).first()
    )


def _index_exists(bind, index_name: str) -> bool:
    """Index presence is asked separately from column presence, deliberately.

    Found by a release rollback drill: nesting the index creation inside the
    column guard made it unreachable on any FRESH database. The baseline
    migration runs Base.metadata.create_all, which builds `neurons` from the
    CURRENT models — so `dormant_at` already exists when this migration runs,
    the column guard skips, and the partial index it was supposed to create
    never appears. Live databases had it (their column was added by this
    migration back when it did not exist); freshly-migrated ones silently did
    not, and downgrade then failed dropping an index that was never made.
    """
    return bool(
        bind.execute(
            text(
                """
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = :index_name
                """
            ),
            {"index_name": index_name},
        ).first()
    )


def upgrade():
    bind = op.get_bind()
    if not _column_exists(bind, "neurons", "homeostatic_weight"):
        # server_default is load-bearing, not cosmetic: existing rows must
        # land at full strength so the first pass cannot demote anything
        # merely for predating the column.
        op.add_column(
            "neurons",
            sa.Column("homeostatic_weight", sa.Float(), nullable=False,
                      server_default="1.0"),
        )
    if not _column_exists(bind, "neurons", "dormant_at"):
        op.add_column("neurons", sa.Column("dormant_at", sa.DateTime(),
                                           nullable=True))
    # Partial index: the dormant set is a small minority and every consumer
    # asks "is this one dormant", never "list them all". Guarded on the INDEX,
    # not the column — see _index_exists.
    if not _index_exists(bind, "ix_neurons_dormant_at"):
        op.create_index("ix_neurons_dormant_at", "neurons", ["dormant_at"],
                        postgresql_where=text("dormant_at IS NOT NULL"))


def downgrade():
    bind = op.get_bind()
    if _index_exists(bind, "ix_neurons_dormant_at"):
        op.drop_index("ix_neurons_dormant_at", table_name="neurons")
    if _column_exists(bind, "neurons", "dormant_at"):
        op.drop_column("neurons", "dormant_at")
    if _column_exists(bind, "neurons", "homeostatic_weight"):
        op.drop_column("neurons", "homeostatic_weight")
