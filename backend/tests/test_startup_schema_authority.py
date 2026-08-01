"""Regression tests for startup schema authority.

These originally parsed a single hardcoded ``lifespan`` in app/main.py. Startup
is now composed per tenant from
:data:`app.composition.capabilities.STARTUP_STEPS`, so the invariant is checked
against the declarative model instead — which strengthens it: the ordering now
has to hold for *every* profile, not just for the one sequence someone wrote
into main.py.
"""

import ast
import inspect
from pathlib import Path

import pytest

from app.composition.capabilities import CAPABILITIES, Capability, STARTUP_STEPS
from app.composition.profiles import CapabilityProfile

_SCHEMA_GUARD = "validate_schema_authority"
_MUTATION_MARKERS = ("_run_migrations", "create_all", "CREATE TABLE", "ALTER TABLE", "CREATE INDEX")


def _startup_source() -> str:
    source = Path(__file__).resolve().parents[1] / "app" / "composition" / "startup.py"
    return source.read_text()


@pytest.mark.hermetic
def test_startup_steps_do_not_reintroduce_hidden_schema_migrations():
    """Startup must seed only; Alembic owns schema mutation."""
    text = _startup_source()
    tree = ast.parse(text)
    seed_functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    ]
    assert seed_functions, "no startup functions found to inspect"

    for node in seed_functions:
        body = (ast.get_source_segment(text, node) or "").upper()
        for marker in _MUTATION_MARKERS:
            assert marker.upper() not in body, (
                f"{node.name} performs schema mutation ({marker}); Alembic is "
                f"the sole schema authority"
            )


@pytest.mark.hermetic
def test_schema_guard_is_unconditional():
    """No capability profile may switch the schema guard off.

    A step with a non-empty ``required_by`` is skippable. The guard proving the
    database is at the packaged head must never be.
    """
    guard = next(s for s in STARTUP_STEPS if s.name == _SCHEMA_GUARD)
    assert guard.required_by == frozenset(), (
        "the schema-authority guard became capability-gated; a profile could "
        "then seed against an unmanaged or drifted schema"
    )


@pytest.mark.hermetic
@pytest.mark.parametrize("granted", [
    frozenset(),
    frozenset({Capability.MEMORY}),
    frozenset({Capability.MEMORY, Capability.KNOWLEDGE_GRAPH}),
    frozenset(Capability),
])
def test_schema_guard_runs_before_any_seed_write_in_every_profile(granted):
    """Ordering holds for the empty profile and the full one alike."""
    profile = CapabilityProfile(tenant_id="test", granted=granted)
    steps = profile.startup_steps()
    names = [s.name for s in steps]

    assert _SCHEMA_GUARD in names, f"schema guard skipped for profile {sorted(granted)}"
    guard_at = names.index(_SCHEMA_GUARD)
    writers = [i for i, name in enumerate(names) if name.startswith("seed_")]
    assert all(guard_at < i for i in writers), (
        f"a seed step precedes the schema guard for profile {sorted(granted)}: {names}"
    )


@pytest.mark.hermetic
def test_every_startup_step_resolves():
    """A typo in the declarative model must not surface as a startup crash."""
    import importlib

    for step in STARTUP_STEPS:
        module = importlib.import_module(step.module)
        assert hasattr(module, step.attr), f"{step.module}:{step.attr} does not exist"


@pytest.mark.hermetic
def test_every_declared_router_resolves():
    """Same for routers: catch a bad module path in the model, not at boot."""
    import importlib

    from fastapi import APIRouter

    for capability, spec in CAPABILITIES.items():
        for router_spec in spec.routers:
            module = importlib.import_module(router_spec.module)
            router = getattr(module, router_spec.attr, None)
            assert isinstance(router, APIRouter), (
                f"{capability.value} declares {router_spec}, which is not an APIRouter"
            )
