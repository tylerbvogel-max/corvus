"""Opt-in destructive smoke tests for disposable PostgreSQL databases.

Set CORVUS_TEST_DATABASE_URL to a database whose name begins with
``corvus_test_`` or ``corvus_migration_``. The tests drop and recreate its
public schema. Set CORVUS_TEST_SNAPSHOT_DUMP to a custom-format pg_dump to also
exercise a representative pre-head restore and upgrade.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from app.services.schema_authority import expected_schema_heads


pytestmark = pytest.mark.database


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CRITICAL_TABLES = {
    # Migration 013 legitimately backfills NULL abstraction types. Every other
    # neuron field remains covered by the stable fingerprint.
    "neurons": ("abstraction_type",),
    "neuron_edges": (),
    "autopilot_proposals": (),
    "chat_sessions": (),
    "roadmap_ledgers": (),
}


def _test_database_url() -> str:
    value = os.environ.get("CORVUS_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("set CORVUS_TEST_DATABASE_URL to run PostgreSQL migration smoke tests")
    parsed = make_url(value)
    database = parsed.database or ""
    if not database.startswith(("corvus_test_", "corvus_migration_")):
        pytest.fail(
            "refusing destructive migration smoke test: database name must start "
            "with corvus_test_ or corvus_migration_"
        )
    return value


def _sync_url(url_text: str) -> str:
    url = make_url(url_text)
    if url.drivername == "postgresql+asyncpg":
        url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _connect(url_text: str):
    return psycopg2.connect(_sync_url(url_text))


def _reset_public_schema(url_text: str) -> None:
    with _connect(url_text) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")


def _run_alembic(url_text: str, *arguments: str) -> float:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": url_text,
            "PYTHONPATH": ".",
            "TENANT_ID": "corvus-mind",
        }
    )
    started = time.monotonic()
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        check=True,
        text=True,
    )
    return time.monotonic() - started


@contextmanager
def _isolated_database(url: str):
    """Bound destructive state to one test even when its assertion fails."""
    _reset_public_schema(url)
    try:
        yield url
    finally:
        _reset_public_schema(url)


@pytest.fixture
def disposable_database():
    """Give each test a clean schema and make teardown unconditional."""
    with _isolated_database(_test_database_url()) as url:
        yield url


def test_disposable_database_context_removes_planted_state():
    url = _test_database_url()
    with _isolated_database(url):
        with _connect(url) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TABLE ci_isolation_probe (id integer primary key)")
            cursor.execute("INSERT INTO ci_isolation_probe VALUES (1)")
    with _connect(url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.ci_isolation_probe')")
        assert cursor.fetchone()[0] is None


def _current_heads(url_text: str) -> tuple[str, ...]:
    with _connect(url_text) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.alembic_version')")
        if cursor.fetchone()[0] is None:
            return ()
        cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        return tuple(row[0] for row in cursor.fetchall())


def _fingerprints(url_text: str) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    with _connect(url_text) as connection, connection.cursor() as cursor:
        for table, excluded_columns in CRITICAL_TABLES.items():
            payload = "to_jsonb(t)"
            if excluded_columns:
                quoted = ", ".join(f"'{column}'" for column in excluded_columns)
                payload = f"(to_jsonb(t) - ARRAY[{quoted}]::text[])"
            cursor.execute(
                f"SELECT count(*), "
                f"md5(coalesce(string_agg(md5(({payload})::text), '' "
                f"ORDER BY ({payload})::text), '')) FROM {table} t"
            )
            count, checksum = cursor.fetchone()
            output[table] = {"rows": count, "stable_checksum": checksum}
    return output


def _extension_and_vector_index_inventory(url_text: str) -> dict[str, object]:
    with _connect(url_text) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension ORDER BY extname")
        extensions = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND "
            "(indexdef ILIKE '% vector_%' OR indexdef ILIKE '% hnsw %' "
            "OR indexdef ILIKE '% ivfflat %') ORDER BY indexname"
        )
        vector_indexes = [list(row) for row in cursor.fetchall()]
    return {"extensions": extensions, "vector_indexes": vector_indexes}


def _pg_restore(url_text: str, dump_path: Path) -> float:
    parsed = make_url(url_text)
    environment = os.environ.copy()
    if parsed.password:
        environment["PGPASSWORD"] = parsed.password
    command = ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error"]
    if parsed.host:
        command.extend(["--host", parsed.host])
    if parsed.port:
        command.extend(["--port", str(parsed.port)])
    if parsed.username:
        command.extend(["--username", parsed.username])
    command.extend(["--dbname", parsed.database, str(dump_path)])
    started = time.monotonic()
    subprocess.run(command, env=environment, check=True, text=True)
    return time.monotonic() - started


def test_empty_database_bootstraps_to_head_without_application_startup(
    disposable_database,
):
    url = disposable_database

    upgrade_seconds = _run_alembic(url, "upgrade", "head")
    check_seconds = _run_alembic(url, "check")

    assert _current_heads(url) == expected_schema_heads()
    with _connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
        )
        assert cursor.fetchone()[0] >= 49
    print(json.dumps({
        "scenario": "empty-bootstrap",
        "upgrade_seconds": round(upgrade_seconds, 3),
        "alembic_check_seconds": round(check_seconds, 3),
        "head": list(expected_schema_heads()),
    }, sort_keys=True))


@pytest.mark.snapshot
def test_representative_pre_head_snapshot_upgrades_without_stable_data_loss(
    disposable_database,
):
    url = disposable_database
    dump_value = os.environ.get("CORVUS_TEST_SNAPSHOT_DUMP", "")
    if not dump_value:
        pytest.skip("set CORVUS_TEST_SNAPSHOT_DUMP to run the restore/upgrade smoke test")
    dump_path = Path(dump_value).resolve()
    if not dump_path.is_file():
        pytest.fail(f"snapshot dump does not exist: {dump_path}")

    restore_seconds = _pg_restore(url, dump_path)
    assert _current_heads(url) != expected_schema_heads()
    before = _fingerprints(url)
    before_inventory = _extension_and_vector_index_inventory(url)

    upgrade_seconds = _run_alembic(url, "upgrade", "head")

    after = _fingerprints(url)
    after_inventory = _extension_and_vector_index_inventory(url)
    assert _current_heads(url) == expected_schema_heads()
    assert after == before
    assert after_inventory == before_inventory
    print(json.dumps({
        "scenario": "snapshot-upgrade",
        "restore_seconds": round(restore_seconds, 3),
        "upgrade_seconds": round(upgrade_seconds, 3),
        "critical_tables": after,
        "database_inventory": after_inventory,
        "head": list(expected_schema_heads()),
    }, sort_keys=True))
