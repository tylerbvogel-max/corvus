"""Add autopilot_proposals and proposal_items tables

Revision ID: 004_add_autopilot_proposals
Revises: 003_remove_agent_columns
Create Date: 2026-04-02 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004_add_autopilot_proposals'
down_revision: Union[str, Sequence[str], None] = '003_remove_agent_columns'
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
    """Create proposal tables and add FK columns."""
    if _table_exists("autopilot_proposals"):
        return
    # Create autopilot_proposals first (referenced by proposal_items and autopilot_runs)
    op.create_table(
        'autopilot_proposals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('autopilot_run_id', sa.Integer(), sa.ForeignKey('autopilot_runs.id'), nullable=True),
        sa.Column('query_id', sa.Integer(), sa.ForeignKey('queries.id'), nullable=True),
        sa.Column('state', sa.String(20), nullable=False, server_default='proposed'),
        sa.Column('gap_source', sa.String(30), nullable=True),
        sa.Column('gap_description', sa.Text(), nullable=True),
        sa.Column('gap_evidence_json', sa.Text(), nullable=True),
        sa.Column('priority_score', sa.Float(), server_default='0.0'),
        sa.Column('llm_reasoning', sa.Text(), nullable=True),
        sa.Column('llm_model', sa.String(50), nullable=True),
        sa.Column('prompt_hash', sa.String(64), nullable=True),
        sa.Column('eval_overall', sa.Integer(), server_default='0'),
        sa.Column('eval_text', sa.Text(), nullable=True),
        sa.Column('reviewed_by', sa.String(100), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('applied_by', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_autopilot_proposals_autopilot_run_id', 'autopilot_proposals', ['autopilot_run_id'])
    op.create_index('ix_autopilot_proposals_query_id', 'autopilot_proposals', ['query_id'])

    # Create proposal_items (references autopilot_proposals)
    op.create_table(
        'proposal_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('autopilot_proposals.id'), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('target_neuron_id', sa.Integer(), sa.ForeignKey('neurons.id'), nullable=True),
        sa.Column('field', sa.String(50), nullable=True),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('neuron_spec_json', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_neuron_id', sa.Integer(), sa.ForeignKey('neurons.id'), nullable=True),
        sa.Column('refinement_id', sa.Integer(), sa.ForeignKey('neuron_refinements.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_proposal_items_proposal_id', 'proposal_items', ['proposal_id'])

    # Add proposal_item_id FK to neurons
    op.add_column('neurons', sa.Column('proposal_item_id', sa.Integer(), sa.ForeignKey('proposal_items.id'), nullable=True))

    # Add proposal_id FK to autopilot_runs
    op.add_column('autopilot_runs', sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('autopilot_proposals.id'), nullable=True))


def downgrade() -> None:
    """Drop proposal tables and FK columns."""
    if _table_exists("autopilot_runs"):
        op.drop_column('autopilot_runs', 'proposal_id')
    if _table_exists("neurons"):
        op.drop_column('neurons', 'proposal_item_id')
    if _table_exists("proposal_items"):
        op.drop_table('proposal_items')
    if _table_exists("autopilot_proposals"):
        op.drop_table('autopilot_proposals')
