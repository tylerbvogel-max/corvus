"""Read-only startup enforcement for the Alembic schema contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class SchemaAuthorityError(RuntimeError):
    """The database cannot safely serve this application build."""


@dataclass(frozen=True)
class SchemaAuthorityStatus:
    current_heads: tuple[str, ...]
    expected_heads: tuple[str, ...]


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def expected_schema_heads() -> tuple[str, ...]:
    """Return the immutable Alembic heads shipped with this application."""
    backend_root = _backend_root()
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    heads = tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
    if not heads:
        raise SchemaAuthorityError(
            "No Alembic head is packaged with this build; startup is unsafe."
        )
    return heads


def require_schema_heads(
    current_heads: tuple[str, ...], expected_heads: tuple[str, ...]
) -> SchemaAuthorityStatus:
    """Fail closed when the connected database is unmanaged, behind, or ahead."""
    current = tuple(sorted(current_heads))
    expected = tuple(sorted(expected_heads))
    if current != expected:
        observed = ", ".join(current) if current else "unmanaged (no alembic_version)"
        wanted = ", ".join(expected)
        raise SchemaAuthorityError(
            "Database schema is not at this build's Alembic head: "
            f"observed {observed}; expected {wanted}. "
            "Run `alembic upgrade head` with the intended tenant/database "
            "before starting Corvus."
        )
    return SchemaAuthorityStatus(current_heads=current, expected_heads=expected)


def _current_heads(sync_connection) -> tuple[str, ...]:
    context = MigrationContext.configure(sync_connection)
    return tuple(sorted(context.get_current_heads()))


async def validate_schema_authority(engine: AsyncEngine) -> SchemaAuthorityStatus:
    """Prove connectivity and schema compatibility without mutating the database."""
    try:
        async with engine.connect() as connection:
            current = await connection.run_sync(_current_heads)
    except SQLAlchemyError as exc:
        raise SchemaAuthorityError(
            "Database connectivity failed before Corvus startup. Verify the "
            "tenant DATABASE_URL, PostgreSQL readiness, and credentials."
        ) from exc
    return require_schema_heads(current, expected_schema_heads())
