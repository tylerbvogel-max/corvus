"""Phase-5 integration matrix fixture — the INPUTS to the kernel replay.

WHY THIS MODULE EXISTS (kernel-replay-ungated, 2026-08-01). These four
review packets used to live inline in tests/replay_nvm_throwaway.py, the
only artifact that exercises the reconsolidation kernel end to end against
a real database, the real one-step lifecycle and the real action bus. That
script is in no merge-gate lane, so when mind-neuron-evidence-frame made
framed construction part of `validate_packet`, every packet here began
failing closed and nothing reported it. The breakage was found by accident
weeks later, having survived the evidence-frame record and four
cycle-breaking seams.

The fix is structural, not cosmetic: the packets live HERE, so the
expensive proof and the cheap always-run guard
(tests/test_kernel_replay_fixture_guard.py) consume the SAME objects. A
copy would rot independently and reproduce the original failure mode.

The frames below are deliberately lexically conservative. `validate_packet`
also enforces a no-invention rule — every CONCRETE signal (path, command,
version, env var, port) in the proposed text must appear in some member —
so these frames reuse the members' own signals and introduce none of their
own. See app/services/reconsolidation/fingerprints.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.golden_frames import framed


@dataclass(frozen=True)
class MemberSpec:
    """A matrix member, as data rather than an ORM row, so the guard can
    validate the packets without a database."""

    id: int
    label: str
    content: str
    scope: str
    invocations: int = 0
    utility: float = 0.5


@dataclass(frozen=True)
class MatrixCase:
    """One row of the Phase-5 matrix: members, the packet the reviewer is
    mocked to return, and the behaviour the replay asserts."""

    key: str
    members: tuple[MemberSpec, ...]
    packet: dict
    expectation: str
    detail: str = ""
    # Only a strict restatement names a survivor; everything else is None.
    expected_canonical_id: int | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def build_neuron(spec: MemberSpec):
    """Materialise a member spec as a Neuron row (replay-side only)."""
    from app.models import Neuron

    return Neuron(
        id=spec.id, layer=3, node_type="lesson", label=spec.label,
        summary=spec.content, content=spec.content, department=spec.scope,
        invocations=spec.invocations, avg_utility=spec.utility,
        authority_level="informational", is_active=True,
    )


# --- strict pair: identical restatement, so identity is RETAINED ---------
# Concrete signals available from the members: cmd:node 22.
_STRICT = MatrixCase(
    key="strict_pair_retain_canonical",
    members=(
        MemberSpec(5001, "Node runtime", "Use Node 22 for this machine.",
                   "Environment", invocations=18, utility=0.7),
        MemberSpec(5002, "Node runtime duplicate",
                   "Use Node 22 for this machine.", "Environment",
                   invocations=4, utility=0.5),
    ),
    packet={
        "facets": [{"kind": "invariant", "text": "Use Node 22 for this machine.",
                    "evidence_member_ids": [5001, 5002]}],
        "proposed_label": "Node runtime",
        "proposed_summary": "Use Node 22",
        "proposed_content": framed(
            "Use Node 22 for this machine.",
            entities="none",
            time_scope="stable-preference",
            context="reinforced — both members state the same runtime "
                    "requirement and neither qualifies it.",
            evidence="both members record that this machine uses Node 22.",
            future_use="check this before installing or running project "
                       "tooling here.",
            likely_queries="Which Node release does this machine use?",
            confidence="high",
            volatility="stable",
        ),
        "proposed_scope": "Environment",
    },
    expectation="retain-canonical",
    detail="a strict two-member restatement keeps the stronger canonical "
           "(5001) and absorbs 5002",
    expected_canonical_id=5001,
)

# --- scoped truths: both true, each only inside its own scope ------------
# Concrete signals available from the members: port:8004.
_SCOPED = MatrixCase(
    key="scoped_truths_no_fuse",
    members=(
        MemberSpec(5003, "Corvus preview port", "Corvus preview uses port 8004.",
                   "Projects"),
        MemberSpec(5004, "Harness preview port",
                   "Harness preview uses port 8004.", "Harness"),
    ),
    packet={
        "facets": [
            {"kind": "exception", "text": "Corvus preview uses port 8004.",
             "evidence_member_ids": [5003]},
            {"kind": "exception", "text": "Harness preview uses port 8004.",
             "evidence_member_ids": [5004]},
        ],
        "proposed_label": "Scoped preview ports",
        "proposed_summary": "Port usage is contextual.",
        "proposed_content": framed(
            "Corvus preview uses port 8004; Harness preview uses port 8004.",
            entities="Corvus, Harness",
            time_scope="stable-preference",
            context="narrowed — each statement holds only inside its own "
                    "scope, so neither generalises to the other.",
            evidence="one member records the Corvus preview port and the "
                     "other records the Harness preview port.",
            future_use="establish which scope is in play before assuming a "
                       "preview port.",
            likely_queries="Which port does the Corvus preview use?",
            confidence="medium",
            volatility="stable",
        ),
        "proposed_scope": None,
    },
    expectation="abstain",
    detail="scoped truths must not queue a fusion proposal at all",
)

# --- contradictory members: unresolved conflict abstains for a human -----
# Concrete signals available from the members: cmd:node 20, cmd:node 22.
_CONFLICT = MatrixCase(
    key="contradictory_members_abstain",
    members=(
        MemberSpec(5005, "Runtime pin A", "The runtime pin is Node 20.",
                   "Environment"),
        MemberSpec(5006, "Runtime pin B", "The runtime pin is Node 22.",
                   "Environment"),
    ),
    packet={
        "facets": [{"kind": "conflict",
                    "text": "Runtime pin is Node 20 versus Node 22.",
                    "evidence_member_ids": [5005, 5006], "resolution": None}],
        "proposed_label": "Conflicting runtime pin",
        "proposed_summary": "Unresolved version conflict.",
        "proposed_content": framed(
            "The runtime pin is recorded as Node 20 by one member and "
            "Node 22 by the other.",
            entities="none",
            time_scope="unknown",
            context="contradicted — the members disagree and nothing here "
                    "settles which pin is current.",
            evidence="one member records Node 20 and the other records "
                     "Node 22.",
            future_use="confirm the live pin with a human before relying on "
                       "either value.",
            likely_queries="Which runtime pin is current?",
            confidence="low",
            volatility="uncertain",
        ),
        "proposed_scope": "Environment",
    },
    expectation="abstain",
    detail="an unresolved conflict abstains rather than inventing a winner",
)

# --- rollback proof: a real synthesis whose apply is made to fail --------
# The members carry NO concrete signals, so this frame must introduce none.
_ROLLBACK = MatrixCase(
    key="mid_transaction_rollback",
    members=(
        MemberSpec(5007, "Rollback A", "Rollback proof fact.", "Harness"),
        MemberSpec(5008, "Rollback B", "Rollback proof fact.", "Harness"),
        MemberSpec(5009, "Rollback C", "Rollback proof fact.", "Harness"),
    ),
    packet={
        "facets": [{"kind": "invariant", "text": "Rollback proof fact.",
                    "evidence_member_ids": [5007, 5008, 5009]}],
        "proposed_label": "Rollback proof synthesis",
        "proposed_summary": "Rollback proof fact.",
        "proposed_content": framed(
            "Every member of this component asserts the same rollback "
            "proof fact.",
            entities="none",
            time_scope="stable-preference",
            context="reinforced — three members assert the same fact and "
                    "none disagrees.",
            evidence="each member records the rollback proof fact.",
            future_use="this component exists to prove that a failed apply "
                       "leaves no partial write behind.",
            likely_queries="What does the rollback proof component assert?",
            confidence="medium",
            volatility="stable",
        ),
        "proposed_scope": "Harness",
    },
    expectation="synthesize-new",
    detail="a three-member component composes a new synthesis; the planted "
           "edge.rewire failure must roll the whole transaction back",
)

MATRIX_CASES: tuple[MatrixCase, ...] = (_STRICT, _SCOPED, _CONFLICT, _ROLLBACK)

# Fixture-shaped rot, kept as a NEGATIVE control. This is the exact shape
# the packets had before the evidence-frame contract landed: plain prose in
# proposed_content. The guard asserts this is still REFUSED, so a guard that
# silently stopped checking anything fails loudly instead of passing.
STALE_PLAINTEXT_PACKET: dict = {
    "facets": [{"kind": "invariant", "text": "Use Node 22 for this machine.",
                "evidence_member_ids": [5001, 5002]}],
    "proposed_label": "Node runtime",
    "proposed_summary": "Use Node 22",
    "proposed_content": "Use Node 22 for this machine.",
    "proposed_scope": "Environment",
}
STALE_PLAINTEXT_MEMBERS: tuple[MemberSpec, ...] = _STRICT.members


def case(key: str) -> MatrixCase:
    for c in MATRIX_CASES:
        if c.key == key:
            return c
    raise KeyError(f"unknown matrix case {key!r}")
