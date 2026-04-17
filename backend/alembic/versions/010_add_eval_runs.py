"""Add eval_runs, eval_run_cases, tenant_config for Pattern #3.

Pattern #3 — Evals as immutable, first-class artifacts.
EvalRun captures a full snapshot (suite, model versions, scoring-engine
version, override snapshot) produced by one execution of a suite. Cases
hold the per-query results. TenantConfig.certified_eval_run_id is the
single pointer that /v1/query stamps on external responses — promoting
a completed run is what ships the certification claim.

Revision ID: 010_eval_runs
Revises: 009_output_violations
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "010_eval_runs"
down_revision: Union[str, None] = "009_output_violations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
        {"t": table_name},
    )
    return result.scalar() is not None


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": index_name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _table_exists("eval_runs"):
        op.create_table(
            "eval_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("suite_name", sa.String(200), nullable=False),
            sa.Column("suite_hash", sa.String(64), nullable=False),
            sa.Column("model_versions", postgresql.JSONB(), nullable=False),
            sa.Column("scoring_engine_version", sa.String(50), nullable=False),
            sa.Column("overrides_snapshot", postgresql.JSONB(), nullable=True),
            sa.Column("tenant_id", sa.String(50), nullable=False),
            sa.Column("summary_json", postgresql.JSONB(), nullable=True),
            sa.Column(
                "status", sa.String(20), nullable=False,
                server_default="running",
            ),
            sa.Column(
                "started_at", sa.DateTime(), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("started_by", sa.String(100), nullable=False),
            sa.Column(
                "action_id", sa.Integer(), sa.ForeignKey("actions.id"),
                nullable=True,
            ),
        )

    for idx_name, cols in (
        ("ix_eval_runs_suite_name", ["suite_name"]),
        ("ix_eval_runs_suite_hash", ["suite_hash"]),
        ("ix_eval_runs_tenant_id", ["tenant_id"]),
        ("ix_eval_runs_status", ["status"]),
        ("ix_eval_runs_action_id", ["action_id"]),
    ):
        if not _index_exists(idx_name):
            op.create_index(idx_name, "eval_runs", cols)

    if not _table_exists("eval_run_cases"):
        op.create_table(
            "eval_run_cases",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "eval_run_id", sa.Integer(),
                sa.ForeignKey("eval_runs.id"), nullable=False,
            ),
            sa.Column("case_label", sa.String(200), nullable=False),
            sa.Column("query_text", sa.Text(), nullable=False),
            sa.Column(
                "query_id", sa.Integer(), sa.ForeignKey("queries.id"),
                nullable=True,
            ),
            sa.Column("response_text", sa.Text(), nullable=True),
            sa.Column("lineage_id", sa.Integer(), nullable=True),
            sa.Column(
                "blocked", sa.Boolean(), nullable=False,
                server_default="false",
            ),
            sa.Column("violations_json", postgresql.JSONB(), nullable=True),
            sa.Column("scores_json", postgresql.JSONB(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(),
                server_default=sa.func.now(),
            ),
        )

    for idx_name, cols in (
        ("ix_eval_run_cases_eval_run_id", ["eval_run_id"]),
        ("ix_eval_run_cases_query_id", ["query_id"]),
        ("ix_eval_run_cases_run_label", ["eval_run_id", "case_label"]),
    ):
        if not _index_exists(idx_name):
            op.create_index(idx_name, "eval_run_cases", cols)

    if not _table_exists("tenant_config"):
        op.create_table(
            "tenant_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "certified_eval_run_id", sa.Integer(),
                sa.ForeignKey("eval_runs.id"), nullable=True,
            ),
            sa.Column("certified_at", sa.DateTime(), nullable=True),
            sa.Column("certified_by", sa.String(100), nullable=True),
            sa.Column(
                "updated_at", sa.DateTime(),
                server_default=sa.func.now(), nullable=False,
            ),
        )
        # Seed the single row so UPDATE-by-id works without an INSERT path
        op.execute(sa.text("INSERT INTO tenant_config (id) VALUES (1) ON CONFLICT DO NOTHING"))


def downgrade() -> None:
    if _table_exists("tenant_config"):
        op.drop_table("tenant_config")

    for idx_name in (
        "ix_eval_run_cases_run_label",
        "ix_eval_run_cases_query_id",
        "ix_eval_run_cases_eval_run_id",
    ):
        if _index_exists(idx_name):
            op.drop_index(idx_name, table_name="eval_run_cases")
    if _table_exists("eval_run_cases"):
        op.drop_table("eval_run_cases")

    for idx_name in (
        "ix_eval_runs_action_id",
        "ix_eval_runs_status",
        "ix_eval_runs_tenant_id",
        "ix_eval_runs_suite_hash",
        "ix_eval_runs_suite_name",
    ):
        if _index_exists(idx_name):
            op.drop_index(idx_name, table_name="eval_runs")
    if _table_exists("eval_runs"):
        op.drop_table("eval_runs")
