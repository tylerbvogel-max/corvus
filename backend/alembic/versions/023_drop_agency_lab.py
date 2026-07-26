"""retire Agency Lab agent-management schema.

Revision ID: 023_drop_agency_lab
Revises: 022_roadmap_ledgers

The synthetic calibration receipts were frozen under eval/agency-archive
before these tables were retired.  Agency Lab was never a production data
domain; execution identity, permissions, and orchestration belong to harnesses.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "023_drop_agency_lab"
down_revision: Union[str, None] = "022_roadmap_ledgers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = (
    "agency_work_order_events",
    "agency_permission_leases",
    "agency_capital_transactions",
    "agency_score_events",
    "agency_work_orders",
    "agency_plan_revisions",
    "agency_venture_plans",
    "agency_experiments",
    "agency_worker_profiles",
    "agency_policies",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade() -> None:
    # This experimental schema is intentionally not recreated. Its behavior
    # remains auditable in git history and the archived calibration receipts.
    pass
