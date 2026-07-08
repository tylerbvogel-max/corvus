"""Drop orphaned corvus_* screen-watcher tables.

The screen-capture/watcher subsystem (backend/app/corvus/, /corvus/*
endpoints, Advisor UI, browser extension) was removed 2026-07-07; its
tables were intentionally left behind at the time so the code removal
stayed non-destructive. This migration finishes the cleanup. The list
includes three tables from an even earlier watcher iteration
(corvus_graph_*, corvus_work_streams) that never had ORM models in the
final codebase. The shared observation_queue intake is NOT touched.

Irreversible by design: the ORM models were deleted, so downgrade
cannot recreate the schema. Historical data (155 captures, 2 sessions,
4 known-app rows in corvus_aero) is recoverable only from DB backups.

Revision ID: 017_drop_corvus_tables
Revises: 016_citation_hopping
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "017_drop_corvus_tables"
down_revision: Union[str, None] = "016_citation_hopping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# CASCADE clears the inter-table FKs (captures -> sessions, etc.) without
# caring about drop order. IF EXISTS keeps this safe on tenant DBs that
# never ran the watcher (e.g. corvus_flow / corvus_plumbing).
_WATCHER_TABLES: tuple[str, ...] = (
    "corvus_advisories",
    "corvus_alert_rules",
    "corvus_attention_items",
    "corvus_captures",
    "corvus_custom_apps",
    "corvus_entities",
    "corvus_graph_edges",
    "corvus_graph_nodes",
    "corvus_interpretations",
    "corvus_known_apps",
    "corvus_sessions",
    "corvus_work_streams",
)


def upgrade() -> None:
    for table in _WATCHER_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade() -> None:
    raise RuntimeError(
        "017_drop_corvus_tables is irreversible — the watcher ORM models "
        "were deleted with the subsystem. Restore from a DB backup instead."
    )
