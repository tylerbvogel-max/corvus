"""Connection-pool boundaries around slow query-provider work."""

import asyncio
import ast
from pathlib import Path

import app.services.executor as executor
from app.database import release_connection_before_external_io
from app.models import Query


_BACKEND = Path(__file__).resolve().parents[1]
_QUERY_DELIVERY_LLM_FILES = (
    _BACKEND / "app/routers/query.py",
    _BACKEND / "app/routers/chat_sessions.py",
    _BACKEND / "app/services/entailment_check.py",
)


class _PhaseSession:
    """Minimal session double that records transaction/persistence ordering."""

    def __init__(self):
        self.events: list[str] = []
        self.added: list[object] = []

    async def commit(self):
        self.events.append("commit")

    def add(self, row):
        self.events.append(f"add:{type(row).__name__}")
        self.added.append(row)

    async def flush(self):
        self.events.append("flush")
        for row in self.added:
            if isinstance(row, Query) and row.id is None:
                row.id = 901


def test_release_boundary_commits_completed_database_phase():
    db = _PhaseSession()

    asyncio.run(release_connection_before_external_io(db))

    assert db.events == ["commit"]


def test_execute_query_releases_connection_and_defers_query_insert(monkeypatch):
    """Honeypot: provider execution must see no Query insert before the release."""
    db = _PhaseSession()
    classify = {
        "classification": {},
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }

    async def fake_acquire(*_args, **_kwargs):
        return {}, classify, None, None

    async def fake_provider_slot(**_kwargs):
        assert db.events == ["commit"], (
            "provider work started before the prep transaction released, or "
            "the Query row was inserted prematurely"
        )
        db.events.append("provider")
        return {
            "mode": "haiku_raw",
            "model": "haiku",
            "neurons": False,
            "response": "answer",
            "input_tokens": 10,
            "output_tokens": 4,
            "cost_usd": 0.001,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "observed_total_input_tokens": 10,
            "token_budget": 4000,
            "top_k": 0,
            "label": "Haiku Raw @ 4K",
        }

    async def fake_update(db_arg, *_args, **_kwargs):
        await db_arg.commit()

    monkeypatch.setattr(executor, "_acquire_query_contexts", fake_acquire)
    monkeypatch.setattr(executor, "_execute_slot", fake_provider_slot)
    monkeypatch.setattr(executor, "_update_counters_and_fire", fake_update)
    monkeypatch.setattr(executor.settings, "citation_hopping_enabled", False)

    result = asyncio.run(executor.execute_query(
        db,
        "Does the pool stay free?",
        slots=[{"mode": "haiku_raw", "token_budget": 4000}],
    ))

    assert db.events == [
        "commit",
        "provider",
        "add:Query",
        "flush",
        "commit",
    ]
    assert result["query_id"] == 901
    assert result["slots"][0]["response"] == "answer"


def test_db_aware_query_llm_callers_release_before_every_direct_call():
    """Fitness function for DB-aware provider callers in query delivery.

    Delegated provider helpers intentionally have no ``db`` parameter; their
    caller is covered by the execution-order honeypot above. Any function that
    can directly access both a DB session and ``llm_chat`` must visibly end the
    DB phase first.
    """
    violations: list[str] = []
    for path in _QUERY_DELIVERY_LLM_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        ):
            arg_names = {arg.arg for arg in fn.args.args}
            if "db" not in arg_names:
                continue
            calls = [
                node for node in ast.walk(fn)
                if isinstance(node, ast.Call)
            ]
            llm_lines = [
                call.lineno for call in calls
                if isinstance(call.func, ast.Name) and call.func.id == "llm_chat"
            ]
            if not llm_lines:
                continue
            release_lines = [
                call.lineno for call in calls
                if isinstance(call.func, ast.Name)
                and call.func.id == "release_connection_before_external_io"
            ]
            for llm_line in llm_lines:
                if not any(line < llm_line for line in release_lines):
                    violations.append(f"{path.name}:{fn.name}:{llm_line}")

    assert violations == [], (
        "DB-aware query provider calls lack a preceding connection-release "
        f"boundary: {violations}"
    )
