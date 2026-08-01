"""Add weak_edges JSONB column to neurons and demote sub-threshold edges.

Edges with weight < 0.10 OR co_fire_count < 2 are moved from the
neuron_edges table into a JSONB column on the neurons table, reducing
table size by ~66%.

Revision ID: 006_add_tiered_edges
Revises: 005_add_integrity_system
Create Date: 2026-04-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '006_add_tiered_edges'
down_revision: Union[str, Sequence[str], None] = '005_add_integrity_system'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Promotion thresholds (must match app.config defaults)
PROMOTE_MIN_WEIGHT = 0.10
PROMOTE_MIN_COFIRES = 2
BATCH_SIZE = 1000


def upgrade() -> None:
    # 1. Add the JSONB column
    conn = op.get_bind()
    weak_edges_exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'neurons' AND column_name = 'weak_edges'"
    )).scalar() is not None
    if weak_edges_exists:
        return
    op.add_column("neurons", sa.Column("weak_edges", JSONB, nullable=True))

    # 2. Demote weak edges into JSONB in batches
    conn = op.get_bind()

    # Count edges to demote
    demote_count = conn.execute(sa.text(
        "SELECT COUNT(*) FROM neuron_edges "
        "WHERE weight < :mw OR co_fire_count < :mc"
    ), {"mw": PROMOTE_MIN_WEIGHT, "mc": PROMOTE_MIN_COFIRES}).scalar()

    if demote_count == 0:
        return

    # Build JSONB blobs grouped by holder (LEAST of source_id, target_id).
    # Process in batches by holder neuron ID to avoid long locks.
    # Get the range of holder IDs
    max_holder = conn.execute(sa.text(
        "SELECT MAX(LEAST(source_id, target_id)) FROM neuron_edges "
        "WHERE weight < :mw OR co_fire_count < :mc"
    ), {"mw": PROMOTE_MIN_WEIGHT, "mc": PROMOTE_MIN_COFIRES}).scalar()

    min_holder = conn.execute(sa.text(
        "SELECT MIN(LEAST(source_id, target_id)) FROM neuron_edges "
        "WHERE weight < :mw OR co_fire_count < :mc"
    ), {"mw": PROMOTE_MIN_WEIGHT, "mc": PROMOTE_MIN_COFIRES}).scalar()

    if max_holder is None:
        return

    batch_start = min_holder
    total_demoted = 0

    while batch_start <= max_holder:
        batch_end = batch_start + BATCH_SIZE

        # Build and apply JSONB blobs for this batch of holder neurons.
        # For each weak edge, holder = LEAST(source_id, target_id),
        # peer = GREATEST(source_id, target_id).
        conn.execute(sa.text("""
            WITH weak AS (
                SELECT
                    LEAST(source_id, target_id) AS holder_id,
                    GREATEST(source_id, target_id) AS peer_id,
                    weight, edge_type, co_fire_count, source,
                    last_updated_query
                FROM neuron_edges
                WHERE (weight < :mw OR co_fire_count < :mc)
                  AND LEAST(source_id, target_id) >= :lo
                  AND LEAST(source_id, target_id) < :hi
            ),
            blobs AS (
                SELECT
                    holder_id,
                    jsonb_object_agg(
                        peer_id::text,
                        jsonb_build_object(
                            'w', weight, 't', edge_type,
                            'c', co_fire_count, 's', source,
                            'q', last_updated_query
                        )
                    ) AS blob
                FROM weak
                GROUP BY holder_id
            )
            UPDATE neurons n
            SET weak_edges = COALESCE(n.weak_edges, '{}'::jsonb) || b.blob
            FROM blobs b
            WHERE n.id = b.holder_id
        """), {
            "mw": PROMOTE_MIN_WEIGHT,
            "mc": PROMOTE_MIN_COFIRES,
            "lo": batch_start,
            "hi": batch_end,
        })

        # Delete the demoted edges from the table
        result = conn.execute(sa.text("""
            DELETE FROM neuron_edges
            WHERE (weight < :mw OR co_fire_count < :mc)
              AND LEAST(source_id, target_id) >= :lo
              AND LEAST(source_id, target_id) < :hi
        """), {
            "mw": PROMOTE_MIN_WEIGHT,
            "mc": PROMOTE_MIN_COFIRES,
            "lo": batch_start,
            "hi": batch_end,
        })
        total_demoted += result.rowcount

        batch_start = batch_end

    print(f"Migration 006: demoted {total_demoted} of {demote_count} weak edges to JSONB")


def downgrade() -> None:
    # Weak edges stored in JSONB are lost on downgrade (acceptable:
    # they were below the useful threshold for spread activation)
    conn = op.get_bind()
    if conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'neurons' AND column_name = 'weak_edges'"
    )).scalar() is not None:
        op.drop_column("neurons", "weak_edges")
