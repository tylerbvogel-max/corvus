"""FusionPlan — the reviewed, hashed contract for one component reconsolidation.

A FusionPlan is produced by component review (Phase 1), approved by a human,
and applied atomically through the Action Bus (Phase 4). It is deterministic
after approval: an LLM may have PROPOSED the synthesis text, but the plan —
not the model — decides statistics, topology, and identity. Every inherited
signal and every rewired edge appears here as an explicit preview before
anything mutates.

Hashing: `member_state_hash` pins the exact member state the plan was written
against; `plan_hash` pins the plan itself. Apply must revalidate both and
fail closed on drift (validators.preflight).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1


class Disposition(str, Enum):
    # Strict two-member restatement with no coverage delta: strongest member
    # keeps its ID; the other is absorbed.
    RETAIN_CANONICAL = "retain-canonical"
    # Any multi-member component, scope correction, conflict resolution, or
    # material coverage delta: a NEW synthesis neuron is created and every
    # prior member (canonical included) is preserved behind it. No winner bias.
    SYNTHESIZE_NEW = "synthesize-new"
    # Contradictory members needing human context — plan is reviewable but
    # can never be applied.
    ABSTAIN = "abstain"


class FacetKind(str, Enum):
    INVARIANT = "invariant"          # shared fact all members assert
    EXAMPLE = "example"              # supporting instance of an invariant
    EXCEPTION = "exception"          # scoped exception to an invariant
    CAVEAT = "caveat"                # limitation/uncertainty to preserve
    CONFLICT = "conflict"            # members genuinely disagree
    ADJACENT = "adjacent"            # unrelated fact that must NOT leak into
                                     # the synthesis (ports, envs, caches...)


class MemberSnapshot(BaseModel):
    """Pinned state of one component member at plan time."""
    neuron_id: int
    content_hash: str = Field(..., min_length=16, max_length=16)  # mind_lint.content_hash
    # Review evidence, copied into the immutable plan so the human can
    # countersign a readable before/after without chasing live rows. These
    # are additive/optional so pre-Phase-5 proposals remain inspectable.
    node_type: str | None = None
    label: str | None = None
    summary: str | None = None
    content: str | None = None
    is_active: bool
    department: str | None = None
    authority_level: str | None = None
    invocations: int = 0
    avg_utility: float = 0.5
    superseded_by: int | None = None
    embedding_sha256: str | None = None


class Facet(BaseModel):
    """One classified claim from the component review packet."""
    kind: FacetKind
    text: str = Field(..., min_length=1)
    # Which members evidence this facet. Never empty: a facet nobody
    # asserts is invented evidence and fails closed.
    evidence_member_ids: list[int] = Field(..., min_length=1)
    # CONFLICT facets must carry an explicit human-reviewable resolution,
    # or the plan's disposition must be ABSTAIN.
    resolution: str | None = None


class InheritancePreview(BaseModel):
    """Field-specific inheritance, computed and visible before approval.

    One rule per signal — never a generic max/mean. Values here are asserted
    as postconditions after apply.
    """
    # Union of distinct query IDs across confirmed members (dedups
    # co-delivery). The ONLY correct invocation rule — fixture proof:
    # sum=809, max-member=321, union=419.
    invocations_union_distinct: int = Field(..., ge=0)
    # The two tempting but rejected alternatives stay visible beside the
    # chosen UNION value. They are receipts, never apply inputs.
    invocations_member_sum: int = Field(default=0, ge=0)
    invocations_member_max: int = Field(default=0, ge=0)
    # Utility is replayed from the birth prior through the normal learning
    # function over unique independent attribution events; membership itself
    # adds zero. Gaps are reported, never laundered into confidence.
    utility_replayed: float = Field(..., ge=0.0, le=1.0)
    utility_events_replayed: int = 0
    utility_events_deduped: int = 0
    utility_provenance_gaps: list[str] = Field(default_factory=list)
    # Derived from the synthesized claim's support — no automatic promotion.
    authority_level: str
    # Earliest supporting evidence / latest independent verification.
    effective_date: str | None = None
    last_verified: str | None = None
    # These are actions, not values: they MUST happen inside the apply
    # transaction and are verified by postconditions.
    embedding_action: str = "regenerate-from-final-text"
    entities_action: str = "re-extract-from-final-text"
    centrality_action: str = "recompute-after-rewiring"


class EdgeRef(BaseModel):
    source_id: int
    target_id: int
    edge_type: str


class ExternalPeerRewire(BaseModel):
    """Recomputed relationship to one external peer: union the historical
    co-firing query sets across members, dedup same-query evidence, and rerun
    the normal edge-learning rule. Existing edge weights are never max/mean'd."""
    peer_id: int
    union_cofire_queries: int = Field(..., ge=0)
    recomputed_weight: float = Field(..., ge=0.0)
    edge_type: str  # stellate/pyramidal from FINAL scopes


class RewiringPreview(BaseModel):
    # Conducting edges internal to the component — all retired (retyped to
    # non-conducting evidence-links or removed), never left to conduct.
    internal_activation_edges_to_retire: list[EdgeRef] = Field(default_factory=list)
    # member -> synthesis provenance: durable weight-1 evidence-links that
    # never conduct spread. History is preserved, not deleted.
    provenance_links_to_create: list[EdgeRef] = Field(default_factory=list)
    external_peers: list[ExternalPeerRewire] = Field(default_factory=list)
    # Inactive/superseded peers dropped unless retained as pure provenance.
    inactive_peers_dropped: list[int] = Field(default_factory=list)


class FusionPlan(BaseModel):
    schema_version: int = SCHEMA_VERSION
    component_member_ids: list[int] = Field(..., min_length=2)
    member_snapshots: list[MemberSnapshot]
    source_pair_verdict_ids: list[int] = Field(default_factory=list)

    disposition: Disposition
    # RETAIN_CANONICAL only; must be a member.
    canonical_neuron_id: int | None = None
    # True whenever the synthesis says more than any single member —
    # forces SYNTHESIZE_NEW even for two members.
    coverage_delta: bool = False

    # Proposed canonical identity (synthesized memory stays a normal
    # lesson/tool-profile/context-scope — never a new neuron type).
    proposed_node_type: str = "lesson"
    proposed_department: str | None = None
    proposed_label: str | None = None
    proposed_summary: str | None = None
    proposed_content: str | None = None

    facets: list[Facet] = Field(default_factory=list)
    inheritance: InheritancePreview | None = None
    rewiring: RewiringPreview | None = None

    @property
    def member_ids(self) -> set[int]:
        return set(self.component_member_ids)

    @model_validator(mode="after")
    def _structural_rules(self) -> "FusionPlan":
        ids = self.component_member_ids
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate member ids in component")
        snap_ids = {s.neuron_id for s in self.member_snapshots}
        if snap_ids != set(ids):
            raise ValueError(
                f"member_snapshots {sorted(snap_ids)} do not exactly cover "
                f"component members {sorted(ids)}"
            )

        if self.disposition is Disposition.RETAIN_CANONICAL:
            if len(ids) != 2:
                raise ValueError(
                    "retain-canonical is only legal for a strict two-member "
                    "restatement; multi-member components must synthesize-new"
                )
            if self.coverage_delta:
                raise ValueError(
                    "coverage delta requires synthesize-new even for two members"
                )
            if self.canonical_neuron_id not in set(ids):
                raise ValueError("canonical_neuron_id must be a component member")
        elif self.disposition is Disposition.SYNTHESIZE_NEW:
            if self.canonical_neuron_id is not None:
                raise ValueError(
                    "synthesize-new creates a NEW neuron; canonical_neuron_id "
                    "must be None (no winner bias)"
                )
            for field in ("proposed_label", "proposed_summary", "proposed_content"):
                if not getattr(self, field):
                    raise ValueError(f"synthesize-new requires {field}")

        for facet in self.facets:
            stray = set(facet.evidence_member_ids) - set(ids)
            if stray:
                raise ValueError(
                    f"facet cites non-member evidence {sorted(stray)}: "
                    f"{facet.text[:60]!r}"
                )
        unresolved = [
            f for f in self.facets
            if f.kind is FacetKind.CONFLICT and not f.resolution
        ]
        if unresolved and self.disposition is not Disposition.ABSTAIN:
            raise ValueError(
                f"{len(unresolved)} unresolved conflict facet(s): disposition "
                "must be abstain until a human supplies context"
            )

        # Every member must evidence at least one non-adjacent facet when a
        # synthesis is proposed — an unrepresented member means the plan
        # claims to absorb content it never examined.
        if self.disposition is not Disposition.ABSTAIN and self.facets:
            represented: set[int] = set()
            for f in self.facets:
                if f.kind is not FacetKind.ADJACENT:
                    represented.update(f.evidence_member_ids)
            missing = set(ids) - represented
            if missing:
                raise ValueError(
                    f"members {sorted(missing)} evidence no facet — "
                    "absorbing unexamined content is forbidden"
                )
        return self

    # -- hashing ------------------------------------------------------------
    def member_state_hash(self) -> str:
        """Pin of the exact member state the plan was authored against."""
        basis = sorted(
            (s.neuron_id, s.content_hash, s.is_active, s.department,
             s.invocations, round(s.avg_utility, 6), s.superseded_by)
            for s in self.member_snapshots
        )
        return hashlib.sha256(json.dumps(basis, default=str).encode()).hexdigest()

    def plan_hash(self) -> str:
        """Content-addressed identity of the full plan (stable field order)."""
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()


def snapshot_of(neuron) -> MemberSnapshot:
    """Build a MemberSnapshot from a live Neuron row (parity with
    mind_lint.content_hash so drift detection matches the verdict store)."""
    from app.services.mind_lint import content_hash

    return MemberSnapshot(
        neuron_id=neuron.id,
        content_hash=content_hash(neuron),
        node_type=neuron.node_type,
        label=neuron.label,
        summary=neuron.summary,
        content=neuron.content,
        is_active=bool(neuron.is_active),
        department=neuron.department,
        authority_level=neuron.authority_level,
        invocations=neuron.invocations or 0,
        avg_utility=neuron.avg_utility if neuron.avg_utility is not None else 0.5,
        superseded_by=neuron.superseded_by,
        embedding_sha256=(
            hashlib.sha256(neuron.embedding.encode()).hexdigest()
            if neuron.embedding else None
        ),
    )
