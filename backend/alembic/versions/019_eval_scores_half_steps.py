"""eval_scores dimension columns integer -> float (half-step scores).

Each counterbalanced judge pass still scores integer 1-5 against the
anchored rubric; the persisted value becomes the UNROUNDED mean of the two
passes (half steps, e.g. 3.5 = passes said 3 and 4). Integer rounding hid
pass disagreement — a (4,5) split displayed as an uncontested 5 while the
verdict criticized the answer (observed on query 590, 2026-07-10).

Existing integer rows survive unchanged (5 -> 5.0); downgrade rounds half
steps half-up back to integers, matching the old reconcile behavior.

Revision ID: 019_eval_scores_half_steps
Revises: 018_citation_relevance
Create Date: 2026-07-10 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "019_eval_scores_half_steps"
down_revision: Union[str, None] = "018_citation_relevance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIMS = ("accuracy", "completeness", "clarity", "faithfulness", "overall")


def _column_type(table_name: str, column_name: str) -> str | None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table_name, "c": column_name},
    )
    row = result.fetchone()
    return row[0] if row else None


def upgrade() -> None:
    for dim in DIMS:
        if _column_type("eval_scores", dim) in {"smallint", "integer", "bigint"}:
            op.alter_column(
                "eval_scores", dim,
                existing_type=sa.Integer(),
                type_=sa.Float(),
                existing_nullable=False,
            )


def downgrade() -> None:
    for dim in DIMS:
        if _column_type("eval_scores", dim) in {"real", "double precision", "numeric"}:
            op.alter_column(
                "eval_scores", dim,
                existing_type=sa.Float(),
                type_=sa.Integer(),
                existing_nullable=False,
                postgresql_using=f"floor({dim} + 0.5)::integer",
            )
