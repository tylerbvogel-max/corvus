"""The authoritative capability model.

One declaration per capability, naming everything that capability owns: the
routers it mounts, the startup work it requires, and the scheduled jobs that
drive it. Nothing else in the codebase may decide whether a surface is active —
that was the old pattern (``require_memory_surface`` returning 404 from inside a
mounted route), and a route that answers 404 is still a route: still in the
OpenAPI document, still holding its imports, still advertising itself.

Two rules keep this honest:

  Modules are named as strings, never imported here. The factory imports only
  what an enabled capability asks for, so a disabled capability's provider
  clients and module-level state are never constructed. That absence is what
  ``imports.<tenant>.json`` measures.

  Startup steps declare which capabilities NEED them, not which capability owns
  them. ``init_actions_registry`` is required by memory, ingestion, and
  governance alike; a one-owner model would have silently broken the memory
  write path the moment governance was switched off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    """Every runtime surface a tenant can be granted.

    CORE is not listed as grantable — it is unconditional. Health, docs, the
    tenant descriptor, and the error contract exist in every profile because a
    profile that cannot answer /health cannot be operated at all.
    """

    MEMORY = "memory"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    INGESTION = "ingestion"
    GOVERNANCE = "governance"
    EVALUATION = "evaluation"
    COMPLIANCE = "compliance"
    OPERATOR = "operator"
    EXTERNAL_API = "external_api"


@dataclass(frozen=True)
class RouterSpec:
    """A router to mount, addressed by import path rather than by reference.

    ``also_requires`` names capabilities that must ALSO be granted for this
    router to mount. It exists because a handful of routers are owned by one
    capability but store their state in another's: roadmap ledgers are an
    operator surface whose rows live in the memory graph, and the reference
    library is ingestion that writes reference memory. Both were previously
    gated by ``require_memory_surface``, a dependency that left the routes
    mounted and answering 404. Declaring the dependency here removes them
    instead, which is the property this record is about.
    """

    module: str
    attr: str = "router"
    also_requires: frozenset[Capability] = field(default_factory=frozenset)

    def is_satisfied(self, granted: frozenset[Capability]) -> bool:
        return self.also_requires <= granted

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.module}:{self.attr}"


@dataclass(frozen=True)
class StartupStep:
    """One lifespan step and the capabilities that require it to have run.

    ``required_by`` empty means unconditional (core startup).
    """

    name: str
    module: str
    attr: str
    required_by: frozenset[Capability] = field(default_factory=frozenset)
    # Steps that mutate seeded rows must run inside the advisory lock that
    # serializes initialization across uvicorn workers.
    needs_canonical_lock: bool = False

    def is_required(self, granted: frozenset[Capability]) -> bool:
        return not self.required_by or bool(self.required_by & granted)


@dataclass(frozen=True)
class CapabilitySpec:
    """What one capability contributes to a composed application."""

    summary: str
    routers: tuple[RouterSpec, ...] = ()
    # systemd timer units that drive this capability by POSTing to its routes.
    # Declared so a disabled capability's orphaned timers are visible rather
    # than silently firing into 404s.
    jobs: tuple[str, ...] = ()


# ── Routers that exist in every profile ────────────────────────────────────
# Nothing here is grantable; these are the operability floor.
CORE_ROUTERS: tuple[RouterSpec, ...] = ()


CAPABILITIES: dict[Capability, CapabilitySpec] = {
    Capability.MEMORY: CapabilitySpec(
        summary="Harness memory organ: recall, remember, and the maintenance "
                "lanes that distill, consolidate, compile, and audit it.",
        routers=(
            RouterSpec("app.routers.recall"),
            RouterSpec("app.routers.distill"),
            RouterSpec("app.routers.janitor"),
            RouterSpec("app.routers.compile"),
            RouterSpec("app.routers.auditor"),
            RouterSpec("app.routers.mind_metrics"),
            RouterSpec("app.routers.capabilities"),
        ),
        jobs=(
            "corvus-mind-distill.timer",
            "corvus-mind-janitor.timer",
            "corvus-mind-compile.timer",
            "corvus-mind-auditor.timer",
        ),
    ),
    Capability.KNOWLEDGE_GRAPH: CapabilitySpec(
        summary="Neuron graph read/write surface and the query pipeline over it.",
        routers=(
            RouterSpec("app.routers.query"),
            RouterSpec("app.routers.neurons"),
            RouterSpec("app.routers.engrams"),
            RouterSpec("app.routers.lineage"),
            RouterSpec("app.routers.regions"),
        ),
    ),
    Capability.INGESTION: CapabilitySpec(
        summary="Getting material into the graph: observations, documents, "
                "reference library, and seed loading.",
        routers=(
            RouterSpec("app.routers.ingest"),
            RouterSpec("app.routers.document_ingest"),
            # Writes reference-tier memory, so it needs the memory graph too.
            RouterSpec("app.routers.reference",
                       also_requires=frozenset({Capability.MEMORY})),
            RouterSpec("app.routers.seeding"),
        ),
    ),
    Capability.GOVERNANCE: CapabilitySpec(
        summary="Proposal review, provenance, integrity checks, and the agent "
                "registry that acts on the graph.",
        routers=(
            RouterSpec("app.routers.proposals"),
            RouterSpec("app.routers.proposals", attr="provenance_router"),
            RouterSpec("app.routers.provenance"),
            RouterSpec("app.routers.integrity"),
            RouterSpec("app.routers.agents"),
            RouterSpec("app.routers.tool_definitions"),
        ),
    ),
    Capability.EVALUATION: CapabilitySpec(
        summary="Evaluation runs, experiment labs, and pipeline performance "
                "telemetry.",
        routers=(
            RouterSpec("app.routers.eval_runs"),
            RouterSpec("app.routers.labs"),
            RouterSpec("app.routers.performance"),
        ),
    ),
    Capability.COMPLIANCE: CapabilitySpec(
        summary="Control frameworks, evidence mapping, and compliance "
                "snapshots. Carries its own provider registry.",
        routers=(
            RouterSpec("app.routers.compliance"),
            RouterSpec("app.compliance.router"),
        ),
    ),
    Capability.OPERATOR: CapabilitySpec(
        summary="Human-facing operation: admin console, architecture atlas, "
                "chat sessions, and roadmap ledgers.",
        routers=(
            RouterSpec("app.routers.admin"),
            RouterSpec("app.routers.architecture"),
            RouterSpec("app.routers.chat_sessions"),
            # Operator-facing, but ledger rows live in the memory graph.
            RouterSpec("app.routers.roadmap_ledgers",
                       also_requires=frozenset({Capability.MEMORY})),
        ),
    ),
    Capability.EXTERNAL_API: CapabilitySpec(
        summary="Contracts for callers outside this process: the versioned /v1 "
                "API and the remote MCP transport.",
        routers=(RouterSpec("app.routers.v1"),),
    ),
}


# ── Startup ────────────────────────────────────────────────────────────────
# Order is significant and is the order declared here.
STARTUP_STEPS: tuple[StartupStep, ...] = (
    StartupStep(
        name="validate_schema_authority",
        module="app.services.schema_authority",
        attr="validate_schema_authority",
    ),
    StartupStep(
        name="load_compliance_registry",
        module="app.compliance.registry",
        attr="load_all",
        required_by=frozenset({Capability.COMPLIANCE}),
    ),
    StartupStep(
        name="init_actions_registry",
        module="app.services.actions.init_registry",
        attr="init_actions_registry",
        required_by=frozenset({
            Capability.MEMORY,
            Capability.INGESTION,
            Capability.GOVERNANCE,
        }),
    ),
    StartupStep(
        name="seed_core_data",
        module="app.composition.startup",
        attr="seed_core_data",
        needs_canonical_lock=True,
    ),
    StartupStep(
        name="auto_embed_neurons",
        module="app.composition.startup",
        attr="auto_embed_neurons",
        needs_canonical_lock=True,
    ),
    StartupStep(
        name="seed_engrams",
        module="app.composition.startup",
        attr="seed_engrams",
        # Engram embeddings are part of the recall prefilter cache, so memory
        # needs them even when the engram ROUTER is not mounted.
        required_by=frozenset({Capability.MEMORY, Capability.KNOWLEDGE_GRAPH}),
        needs_canonical_lock=True,
    ),
    StartupStep(
        name="seed_compliance",
        module="app.composition.startup",
        attr="seed_compliance",
        required_by=frozenset({Capability.COMPLIANCE}),
        needs_canonical_lock=True,
    ),
    StartupStep(
        name="preload_hot_caches",
        module="app.composition.startup",
        attr="preload_hot_caches",
        required_by=frozenset({Capability.MEMORY, Capability.KNOWLEDGE_GRAPH}),
    ),
    StartupStep(
        name="cleanup_llm_session_transcripts",
        module="app.composition.startup",
        attr="cleanup_llm_session_transcripts",
        required_by=frozenset({Capability.OPERATOR}),
    ),
)


def capability_of(name: str) -> Capability:
    """Parse a capability name, failing closed on anything unrecognized."""
    try:
        return Capability(name)
    except ValueError:
        known = ", ".join(sorted(c.value for c in Capability))
        raise ValueError(
            f"unknown capability {name!r}; known capabilities are: {known}"
        ) from None
