"""shared cache revisions for horizontally coherent bounded replicas.

Revision ID: 024_cache_coherence
Revises: 023_drop_agency_lab
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "024_cache_coherence"
down_revision: Union[str, None] = "023_drop_agency_lab"
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


_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION bump_semantic_embedding_cache_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO cache_versions (key, revision)
    VALUES ('semantic_embeddings', 2)
    ON CONFLICT (key) DO UPDATE
    SET revision = cache_versions.revision + 1;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS neurons_semantic_cache_insert_delete ON neurons;
CREATE TRIGGER neurons_semantic_cache_insert_delete
AFTER INSERT OR DELETE ON neurons
FOR EACH STATEMENT EXECUTE FUNCTION bump_semantic_embedding_cache_revision();

DROP TRIGGER IF EXISTS neurons_semantic_cache_update ON neurons;
CREATE TRIGGER neurons_semantic_cache_update
AFTER UPDATE OF embedding, is_active ON neurons
FOR EACH STATEMENT EXECUTE FUNCTION bump_semantic_embedding_cache_revision();

DROP TRIGGER IF EXISTS engrams_semantic_cache_insert_delete ON engrams;
CREATE TRIGGER engrams_semantic_cache_insert_delete
AFTER INSERT OR DELETE ON engrams
FOR EACH STATEMENT EXECUTE FUNCTION bump_semantic_embedding_cache_revision();

DROP TRIGGER IF EXISTS engrams_semantic_cache_update ON engrams;
CREATE TRIGGER engrams_semantic_cache_update
AFTER UPDATE OF embedding, is_active ON engrams
FOR EACH STATEMENT EXECUTE FUNCTION bump_semantic_embedding_cache_revision();
"""


def upgrade() -> None:
    if not _table_exists("cache_versions"):
        op.create_table(
            "cache_versions",
            sa.Column("key", sa.String(length=80), nullable=False),
            sa.Column("revision", sa.BigInteger(), server_default="1", nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )
    op.execute(
        "INSERT INTO cache_versions (key, revision) "
        "VALUES ('semantic_embeddings', 1) ON CONFLICT (key) DO NOTHING"
    )
    op.execute(_TRIGGER_SQL)


def downgrade() -> None:
    for trigger, table in (
        ("neurons_semantic_cache_insert_delete", "neurons"),
        ("neurons_semantic_cache_update", "neurons"),
        ("engrams_semantic_cache_insert_delete", "engrams"),
        ("engrams_semantic_cache_update", "engrams"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS bump_semantic_embedding_cache_revision()")
    if _table_exists("cache_versions"):
        op.drop_table("cache_versions")
