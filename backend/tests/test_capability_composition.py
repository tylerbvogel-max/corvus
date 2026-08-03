"""Negative tests: a disabled capability must be ABSENT, not merely refused.

The distinction this file exists to enforce: before the factory, memory
endpoints on a non-memory tenant were mounted and answered 404 from inside a
dependency. That route was still in the OpenAPI document, still held its
imports, and still constructed whatever its module built at import time. A
capability that is off must leave nothing behind to refuse with.

Subprocess-based absence proofs live in ``capability_snapshot.py --check``
(run in CI): sys.modules cannot prove a module was never imported inside a
pytest process that imported it.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter

from app.composition.capabilities import CAPABILITIES, Capability, STARTUP_STEPS
from app.composition.factory import create_app
from app.composition.profiles import CapabilityProfile, ProfileError, resolve_profile
from scripts.capability_snapshot import mounted_paths


class _FakeTenant:
    """Minimal tenant stand-in so profiles can be composed off-disk."""

    def __init__(self, tenant_id="test-tenant", capabilities=None):
        self.tenant_id = tenant_id
        self.display_name = "Test Tenant"
        self.description = "composition test"
        self.seed_prompts = []
        self.declared_capabilities = capabilities


def _profile(*capabilities: Capability) -> CapabilityProfile:
    return CapabilityProfile(tenant_id="test", granted=frozenset(capabilities))


# Real paths owned solely by a single non-memory capability — ingestion and
# evaluation respectively — so a memory-only profile must not serve them. These
# were /admin/frameworks and /admin/evidence-map until record 04a retired the
# compliance capability on 2026-08-01; the exemplar changed, the property did
# not. Every test that asserts their absence also asserts their presence in the
# full profile first, so none of them can pass vacuously.
_GATED_ONLY_PATHS = ("/admin/documents/upload", "/admin/eval/runs")


def _paths(app) -> set[str]:
    """What this app actually serves.

    Delegated, not reimplemented. Reading app.routes here would have been a
    correct spelling until fastapi 0.138.0 stopped flattening included routers
    into it, at which point every assertion below would have read "this
    capability owns nothing" and passed for the wrong reason. See
    scripts.capability_snapshot.effective_routes.
    """
    return mounted_paths(app)


# ── Absence, not refusal ───────────────────────────────────────────────────

@pytest.mark.hermetic
@pytest.mark.parametrize("capability", list(Capability))
def test_disabled_capability_mounts_none_of_its_routes(capability):
    """Every route a capability owns disappears when it is not granted."""
    others = frozenset(Capability) - {capability}
    with_it = create_app(profile=_profile(*Capability), tenant=_FakeTenant())
    without_it = create_app(profile=_profile(*others), tenant=_FakeTenant())

    owned_only_by_this = _paths(with_it) - _paths(without_it)
    if not CAPABILITIES[capability].routers:
        return  # capability contributes no routers; nothing to assert

    assert owned_only_by_this, (
        f"disabling {capability.value} removed no routes at all — it is either "
        f"unowned or still mounted by another capability"
    )
    for path in owned_only_by_this:
        assert path not in _paths(without_it)


@pytest.mark.hermetic
def test_disabled_capability_routes_are_absent_from_openapi():
    """Absence must reach the published contract, not just the router table."""
    full = create_app(profile=_profile(*Capability), tenant=_FakeTenant())
    memory_only = create_app(profile=_profile(Capability.MEMORY), tenant=_FakeTenant())

    full_paths = set(full.openapi()["paths"])
    reduced_paths = set(memory_only.openapi()["paths"])

    # Guard against asserting the absence of something that never existed:
    # a path must be present in the full contract before its absence means
    # anything. (An earlier version of this test asserted a path that no
    # profile served, and passed for no reason.)
    for gated_path in _GATED_ONLY_PATHS:
        assert gated_path in full_paths, (
            f"{gated_path} is not served by any profile; this assertion "
            f"would pass vacuously"
        )
        assert gated_path not in reduced_paths

    assert reduced_paths < full_paths, "reduced profile did not shrink the contract"
    # And the memory surface it DOES have is genuinely published.
    assert "/recall" in reduced_paths


@pytest.mark.hermetic
def test_disabled_capability_does_not_answer_with_a_later_authorization_failure():
    """The old failure mode: route present, 404 raised from a dependency.

    A path owned solely by a disabled capability must not resolve to any route
    at all — including the SPA catch-all, which would hand a client HTML and
    a 200 where it expected an API contract.
    """
    from starlette.testclient import TestClient

    memory_only = create_app(profile=_profile(Capability.MEMORY), tenant=_FakeTenant())
    # No `with`: entering the context would run the lifespan, which needs a
    # database. A GET touches no write path, so the request itself is hermetic.
    client = TestClient(memory_only, raise_server_exceptions=False)

    full = create_app(profile=_profile(*Capability), tenant=_FakeTenant())
    served_when_enabled = _paths(full)

    for gated_path in _GATED_ONLY_PATHS:
        assert gated_path in served_when_enabled, (
            f"{gated_path} is served by no profile; asserting its 404 "
            f"would prove nothing"
        )
        response = client.get(gated_path)
        assert response.status_code == 404, (
            f"{gated_path} answered {response.status_code}; a disabled "
            f"capability must not resolve to a route at all"
        )
        # The SPA catch-all must not have swallowed it and returned the shell.
        assert "text/html" not in response.headers.get("content-type", ""), (
            f"{gated_path} returned the SPA shell instead of 404 — a "
            f"client would read that as the endpoint existing"
        )

    # Control: the granted capability really is reachable on the same app.
    # POST with no body — FastAPI validates and returns 422 before the handler
    # touches a database, so reaching 422 proves the route is mounted. (A GET
    # would 404 here: /recall is POST-only, and the SPA guard answers 404
    # rather than 405 for unmatched methods on API prefixes.)
    assert client.post("/recall", json={}).status_code == 422


# ── Startup dependencies and providers ─────────────────────────────────────

@pytest.mark.hermetic
def test_disabled_capability_startup_steps_do_not_run():
    """A capability off ⇒ the steps it requires are never scheduled.

    This used to be demonstrated with compliance's registry load and seed.
    Record 04a retired that capability on 2026-08-01, so the gate is shown
    against operator's transcript cleanup instead. The property under test is
    the gating itself, not the particular step.
    """
    names = {s.name for s in _profile(Capability.MEMORY).startup_steps()}
    assert "cleanup_llm_session_transcripts" not in names
    # ...and on, it is.
    with_operator = {s.name for s in _profile(Capability.OPERATOR).startup_steps()}
    assert "cleanup_llm_session_transcripts" in with_operator


@pytest.mark.hermetic
def test_external_api_off_means_no_mcp_transport():
    """The MCP session manager and FastMCP server are import-time objects.

    Not mounting /mcp is the visible half; not importing app.mcp_http is the
    half that matters, and CI's snapshot check proves it in a clean process.
    """
    without = create_app(profile=_profile(Capability.MEMORY), tenant=_FakeTenant())
    assert not any(p.startswith("/mcp") for p in _paths(without))

    with_external = create_app(
        profile=_profile(Capability.MEMORY, Capability.EXTERNAL_API), tenant=_FakeTenant()
    )
    assert any(p.startswith("/mcp") for p in _paths(with_external))


@pytest.mark.hermetic
def test_actions_registry_survives_governance_being_disabled():
    """The memory write path needs the action bus even with governance off.

    This is the bug a one-owner startup model would have shipped: attaching
    init_actions_registry to GOVERNANCE alone silently breaks distill/janitor
    saves on a memory-only tenant.
    """
    memory_only = {s.name for s in _profile(Capability.MEMORY).startup_steps()}
    assert "init_actions_registry" in memory_only


@pytest.mark.hermetic
def test_recall_prefilter_keeps_its_engram_seed_without_the_engram_router():
    """Engram embeddings feed the recall cache even when engrams aren't served."""
    memory_only = {s.name for s in _profile(Capability.MEMORY).startup_steps()}
    assert "seed_engrams" in memory_only
    assert "preload_hot_caches" in memory_only


# ── Core surface is unconditional ──────────────────────────────────────────

@pytest.mark.hermetic
def test_core_surface_exists_in_the_empty_profile():
    """A profile granting nothing is still operable and still honest."""
    bare = create_app(profile=_profile(), tenant=_FakeTenant())
    paths = _paths(bare)
    for required in ("/health", "/tenant", "/tenants"):
        assert required in paths, f"{required} vanished from the core surface"


@pytest.mark.hermetic
def test_profile_is_published_for_the_frontend():
    """Verification #6: the UI learns what to advertise from the backend."""
    from starlette.testclient import TestClient

    app = create_app(profile=_profile(Capability.MEMORY), tenant=_FakeTenant())
    client = TestClient(app)  # /tenant touches no database
    payload = client.get("/tenant").json()

    assert payload["capabilities"] == ["memory"]
    assert "ingestion" in payload["disabled_capabilities"]
    assert payload["memory_surface"] is True


# ── Fail closed ────────────────────────────────────────────────────────────

@pytest.mark.hermetic
def test_missing_capabilities_block_fails_closed():
    """Silence is not consent: no block ⇒ refuse to start, not run everything."""
    with pytest.raises(ProfileError, match="declares no 'capabilities:' block"):
        resolve_profile(_FakeTenant(capabilities=None))


@pytest.mark.hermetic
def test_unknown_capability_name_fails_closed_with_an_actionable_error():
    with pytest.raises(ProfileError) as exc:
        resolve_profile(_FakeTenant(capabilities=["memory", "telepathy"]))
    message = str(exc.value)
    assert "telepathy" in message
    assert "known capabilities are" in message
    assert "knowledge_graph" in message  # the actionable part


@pytest.mark.hermetic
@pytest.mark.parametrize("malformed", ["memory", {"memory": True}, 42])
def test_malformed_capabilities_block_fails_closed(malformed):
    with pytest.raises(ProfileError, match="must be a list"):
        resolve_profile(_FakeTenant(capabilities=malformed))


@pytest.mark.hermetic
def test_duplicate_capability_fails_closed():
    with pytest.raises(ProfileError, match="more than once"):
        resolve_profile(_FakeTenant(capabilities=["memory", "memory"]))


@pytest.mark.hermetic
def test_non_string_capability_fails_closed():
    with pytest.raises(ProfileError, match="non-string capability"):
        resolve_profile(_FakeTenant(capabilities=["memory", None]))


# ── Model integrity ────────────────────────────────────────────────────────

@pytest.mark.hermetic
def test_no_router_is_mounted_twice():
    """Duplicate mounting would double every operation in the contract."""
    full = _profile(*Capability)
    specs = full.routers()
    assert len(specs) == len({(s.module, s.attr) for s in specs})


@pytest.mark.hermetic
def test_every_capability_declares_a_summary():
    for capability in Capability:
        assert CAPABILITIES[capability].summary.strip(), (
            f"{capability.value} has no summary; the model is the documentation"
        )


@pytest.mark.hermetic
def test_declared_jobs_belong_to_a_granted_capability():
    """Scheduled units are owned, so orphans are detectable."""
    memory = _profile(Capability.MEMORY)
    assert "corvus-mind-distill.timer" in memory.jobs()
    assert _profile(Capability.EXTERNAL_API).jobs() == ()


# ── Carry-forward 1: the 404-dependency gate is gone ───────────────────────

@pytest.mark.hermetic
def test_memory_surface_gate_no_longer_exists():
    """require_memory_surface must not come back.

    It answered 404 from inside a mounted route, which is precisely the
    "later authorization failure" this record rejects. Composition decides
    this now, so a reintroduction would mean two authorities again.
    """
    import ast
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in (backend / "app").rglob("*.py"):
        # Parsed, not grepped: the comment recording why the gate was removed
        # mentions it by name, and a text search would flag its own epitaph.
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            named = (
                (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name)
                or (isinstance(node, ast.Name) and node.id)
                or (isinstance(node, ast.Attribute) and node.attr)
                or (isinstance(node, ast.alias) and node.name)
            )
            if named == "require_memory_surface":
                offenders.append(str(path.relative_to(backend)))
                break
    assert not offenders, (
        f"require_memory_surface is defined or called again in {offenders}; "
        f"capability composition is the single authority for memory gating"
    )


@pytest.mark.hermetic
def test_memory_backed_routers_need_memory_even_when_their_owner_is_granted():
    """Roadmap ledgers and the reference library store rows in the memory graph.

    Granting OPERATOR or INGESTION without MEMORY must not mount them — that
    is the behavior require_memory_surface used to provide, now declared.
    """
    operator_only = create_app(profile=_profile(Capability.OPERATOR), tenant=_FakeTenant())
    assert not any(
        p.startswith("/roadmap-ledgers") for p in _paths(operator_only)
    ), "roadmap ledgers mounted without the memory graph that stores them"

    with_memory = create_app(
        profile=_profile(Capability.OPERATOR, Capability.MEMORY), tenant=_FakeTenant()
    )
    assert any(
        p.startswith("/roadmap-ledgers") for p in _paths(with_memory)
    ), "roadmap ledgers absent even with both capabilities granted"

    ingestion_only = create_app(profile=_profile(Capability.INGESTION), tenant=_FakeTenant())
    assert not any(
        p.startswith("/admin/reference") for p in _paths(ingestion_only)
    ), "reference library mounted without the memory graph it writes to"


# ── Carry-forward 2: job ownership ─────────────────────────────────────────

@pytest.mark.hermetic
def test_every_declared_job_targets_a_route_its_capability_mounts():
    """A claimed timer must POST to a route the claiming profile serves.

    The maintenance lanes are systemd timers curling the app's own routes, so
    job ownership is route ownership and is checkable without systemd.
    """
    import pathlib
    import re

    backend = pathlib.Path(__file__).resolve().parents[1]
    systemd = backend.parent / "harness" / "systemd"
    exec_url = re.compile(r"ExecStart=.*?-X\s+POST\s+\"?(?P<url>https?://[^\s\"?]+)")

    for capability, spec in CAPABILITIES.items():
        if not spec.jobs:
            continue
        app = create_app(profile=_profile(capability), tenant=_FakeTenant())
        mounted = _paths(app)
        for unit in spec.jobs:
            service = systemd / (unit.removesuffix(".timer") + ".service")
            assert service.exists(), f"{capability.value} claims {unit}, absent from harness/systemd"
            match = exec_url.search(service.read_text())
            assert match, f"{service.name} has no HTTP POST ExecStart to reconcile"
            path = "/" + match.group("url").split("/", 3)[-1]
            assert path in mounted, (
                f"{capability.value} claims {unit} -> POST {path}, but does not "
                f"mount that route; the timer would fire into a 404"
            )


# ── Record 04, seam 1: operator surfaces leave the compliance context ──────
#
# routers/compliance.py owned /admin/system-banner and /admin/audit-log* on the
# strength of a control citation (AC-8, AU-2/3/6/7) rather than of ownership.
# The cost was measured, not theoretical: composing `compliance` away from
# corvus-mind removed the system use notification, and because SystemUseBanner
# fails soft on 404, it disappeared in silence. These tests hold the split.

# Operator surfaces that used to be mounted by the compliance capability.
_OPERATOR_SURFACES = (
    "/admin/system-banner",
    "/admin/audit-log",
    "/admin/audit-log/summary",
)

# Where those routes are allowed to be declared, now that the context they came
# from is gone. Record 04a retired compliance on 2026-08-01, so the old guard —
# "compliance must not re-declare these" — has no file left to read. The durable
# form is the inverse: exactly one module may declare them.
_OPERATOR_SURFACE_OWNER = "app/operations/router.py"


@pytest.mark.hermetic
def test_operator_surfaces_are_owned_by_operator_alone():
    """The gate record 04 names, in both directions.

    This is the regression that blocked the drop for a full record cycle: the
    system use notification vanished on profiles that did not grant compliance,
    and because SystemUseBanner fails soft on 404, it disappeared in silence.
    Operator alone must mount these, and no profile without operator may.
    """
    operator_only = create_app(profile=_profile(Capability.OPERATOR), tenant=_FakeTenant())
    missing = [p for p in _OPERATOR_SURFACES if p not in _paths(operator_only)]
    assert not missing, f"operator alone does not mount the surfaces it owns: {missing}"

    without_operator = create_app(
        profile=_profile(*(frozenset(Capability) - {Capability.OPERATOR})),
        tenant=_FakeTenant(),
    )
    leaked = [p for p in _OPERATOR_SURFACES if p in _paths(without_operator)]
    assert not leaked, (
        f"{leaked} mounted on a profile without operator; some other capability "
        f"has taken ownership of an operator surface again"
    )


@pytest.mark.hermetic
def test_operator_routes_are_declared_in_exactly_one_module():
    """Source-level guard: only app/operations/ may declare these paths.

    The composition test above checks the mounted result, which a second
    declaration elsewhere would still satisfy on a full profile. This reads the
    route decorators directly across the whole app, so ownership drift fails
    here first — the way it did when a control-family citation was mistaken for
    ownership. See app/operations/__init__.py.
    """
    import ast
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for source in sorted((backend / "app").rglob("*.py")):
        rel = source.relative_to(backend).as_posix()
        if rel == _OPERATOR_SURFACE_OWNER:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                # @router.get("/path"), @router.post("/path"), ...
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                declared = decorator.args[0].value
                if not isinstance(declared, str) or not declared.strip("/"):
                    # @router.get("") is the router's own root, not a surface.
                    continue
                # Routers carry prefix="/admin"; compare on the suffix.
                if any(s.endswith(declared) for s in _OPERATOR_SURFACES):
                    offenders.append(f"{rel}:{node.lineno} {declared}")

    assert not offenders, (
        f"operator routes declared outside {_OPERATOR_SURFACE_OWNER}: {offenders}. "
        f"A control-family citation is not ownership."
    )


@pytest.mark.hermetic
def test_audit_trail_is_readable_wherever_it_is_written():
    """The reader must not be composable away while the writer is not.

    AuditMiddleware is installed by the factory on every profile, including the
    empty one, so every deployment accumulates audit rows. A profile that wrote
    them and could not read them back would be audited and blind — which is
    exactly what the old compliance ownership produced.
    """
    from app.middleware.audit import AuditMiddleware

    empty = create_app(profile=_profile(), tenant=_FakeTenant())
    installed = [m.cls for m in empty.user_middleware]
    assert AuditMiddleware in installed, (
        "AuditMiddleware is no longer unconditional; if audit writing became "
        "capability-gated, the read surface's ownership must be revisited "
        "alongside it rather than left behind."
    )

    operator = create_app(profile=_profile(Capability.OPERATOR), tenant=_FakeTenant())
    mounted = _paths(operator)
    assert "/admin/audit-log" in mounted, (
        "audit rows are written on every profile, but the operator capability "
        "cannot read them back"
    )
