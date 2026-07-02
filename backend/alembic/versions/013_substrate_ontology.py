"""Substrate/ontology split — abstraction axis + centrality (plat-substrate-ontology).

Demotes the 6-layer org chart from engine spine to navigational projection:

- `neurons.abstraction_type` — the universal knowledge-kind gradient
  (structural | concept | principle | process | procedure | artifact),
  classifiable from content alone. Engine predicates key on this;
  numeric `layer` survives as depth-in-projection metadata only.
  Backfilled from node_type via the same mapping as
  `app.models.ABSTRACTION_BY_NODE_TYPE`.
- `neurons.centrality` — normalized degree centrality over promoted edges,
  denormalized for the cold-start scoring prior (authority + freshness +
  centrality), refreshed by consolidation.

Revision ID: 013_substrate_ontology
Revises: 012_autopilot_stage_telemetry
Create Date: 2026-07-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "013_substrate_ontology"
down_revision: Union[str, None] = "012_autopilot_stage_telemetry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Kept in sync with app.models.ABSTRACTION_BY_NODE_TYPE (duplicated here so
# the migration is self-contained and immune to later mapping evolution).
_BACKFILL_CASES = (
    ("department", "structural"),
    ("role", "structural"),
    ("concept", "concept"),
    ("task", "process"),
    ("process", "process"),
    ("system", "procedure"),
    ("procedure", "procedure"),
    ("technique", "procedure"),
    ("control", "procedure"),
    ("decision", "principle"),
    ("knowledge", "principle"),
    ("standard", "principle"),
    ("reference", "principle"),
    ("output", "artifact"),
    ("artifact", "artifact"),
    ("detail", "artifact"),
    ("metric", "artifact"),
)


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


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :i"),
        {"i": index_name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists("neurons", "abstraction_type"):
        op.add_column(
            "neurons",
            sa.Column("abstraction_type", sa.String(length=20), nullable=True),
        )
    if not _index_exists("ix_neurons_abstraction_type"):
        op.create_index("ix_neurons_abstraction_type", "neurons", ["abstraction_type"])

    if not _column_exists("neurons", "centrality"):
        op.add_column(
            "neurons",
            sa.Column(
                "centrality", sa.Float(), nullable=False, server_default="0.0",
            ),
        )

    when_clauses = " ".join(
        f"WHEN '{node_type}' THEN '{abstraction}'"
        for node_type, abstraction in _BACKFILL_CASES
    )
    op.execute(
        sa.text(
            "UPDATE neurons SET abstraction_type = "
            f"CASE node_type {when_clauses} ELSE NULL END "
            "WHERE abstraction_type IS NULL"
        )
    )


def downgrade() -> None:
    if _index_exists("ix_neurons_abstraction_type"):
        op.drop_index("ix_neurons_abstraction_type", table_name="neurons")
    if _column_exists("neurons", "abstraction_type"):
        op.drop_column("neurons", "abstraction_type")
    if _column_exists("neurons", "centrality"):
        op.drop_column("neurons", "centrality")
