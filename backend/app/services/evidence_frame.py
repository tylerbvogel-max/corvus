"""Evidence frame — the construction contract for durable memory
(mind-neuron-evidence-frame).

WHY THIS EXISTS: Corvus has invested heavily in retrieval, graph
connection, spread activation, and token-bounded assembly, but neuron
CONTENT stayed whatever an agent happened to type. The LoCoMo Step 06
result (2026-07-28) closed the retrieve-more door: second-hop retrieval
fired on 4/25 memory-smoke questions and admitted 12 extra memories, yet
Oracle Funnel measured 0% assembly loss — the misses were not "we failed
to fetch enough." They were topically-right neurons that could not
reconstruct the answer. This module makes that failure mode expensive to
create instead of expensive to diagnose.

A durable memory is an evidence-bearing answer capsule, not a prose blob.
Connections decide how memories MEET each other; the frame decides whether
a memory can still ANSWER once it arrives.

THE CONTRACT: nine slots, fixed order, machine-checked before any durable
write. Missing headings, empty required slots, out-of-order slots, or
uncontrolled vocabulary fail closed — the write raises rather than
degrading. Centralizing the check is the point: every construction path
(distiller, janitor fusion, dedup/consolidation, reconsolidation, proposal
approval, LoCoMo ingest) shares this one parser, so there is no cheap way
for an agent to write around it.

TWO SLOTS EARN THEIR KEEP BEYOND ANSWERABILITY:
- `Volatility` supplies at WRITE time what the staleness janitor's
  durability gate (mind-identity-fact-supersession, Step 04) currently has
  to INFER post-hoc from a pairwise classifier. That gate found only 9 of
  29 label-recoverable supersessions were genuine state updates; 13 fired
  on compatible pairs and took durable casualties. An author declaring
  `perishable` vs `stable` at construction is strictly better evidence
  than a downstream classifier guessing.
- `Time scope` forces the dated-event / stable-preference / current-plan /
  expired-fact distinction that janitor fusion was observed to mush
  together (the certificate's "janitor fusion loses dates" finding).

HONEST ABSENCE IS ALLOWED; INVENTION IS NOT. `Context` and `Entities`
accept explicit unknown/none markers, because the record's standing rule
is to record missing motivation as unknown rather than hallucinate it.
`Evidence` accepts no such marker — a memory with no citable source has
nothing to be audited against and must not be written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

# Fixed slot order. Order is enforced, not merely membership: a stable
# shape keeps the frame diffable, keeps prompts honest, and makes the
# semantic/metadata split below a positional fact rather than a lookup.
CANONICAL_SLOTS: tuple[str, ...] = (
    "Claim",
    "Entities",
    "Time scope",
    "Context",
    "Evidence",
    "Future-use",
    "Likely queries",
    "Confidence",
    "Volatility",
)

# Slots that may honestly report absence, and the markers that do it.
# Everything else must carry substance.
_ABSENCE_OK = frozenset({"Entities", "Context"})
_ABSENCE_MARKERS = frozenset({"unknown", "unstated", "none", "n/a", "not stated"})

# Controlled vocabularies. The leading token of the slot value must match;
# a trailing qualifier is allowed and preserved (e.g. "dated-event (2026-07-21)").
TIME_SCOPE_KINDS: tuple[str, ...] = (
    "dated-event",      # happened at a specific time
    "stable-preference",  # holds until explicitly revoked
    "current-plan",     # intent that expires by being executed or abandoned
    "expired-fact",     # known to be no longer true; kept for history
    "unknown",          # timing genuinely unstated in the source
)
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")
# Deliberately mirrors the staleness gate's vocabulary rather than
# inventing a parallel one: `perishable` is the class recency is allowed
# to arbitrate, `stable` is the class it must never auto-retire.
VOLATILITY_LEVELS: tuple[str, ...] = ("stable", "perishable", "uncertain")

_VOCAB: MappingProxyType = MappingProxyType({
    "Time scope": TIME_SCOPE_KINDS,
    "Confidence": CONFIDENCE_LEVELS,
    "Volatility": VOLATILITY_LEVELS,
})

# Node types whose content IS a durable memory claim. Structural
# scaffolding (project containers, roles, departments, compiled skills,
# document shells) carries no claim to reconstruct and is exempt by
# CLASSIFICATION, not by an opt-out flag — there is no per-write bypass.
FRAMED_NODE_TYPES = frozenset({"lesson", "context-scope", "tool-profile"})

# Slots that describe the memory's SUBSTANCE, in canonical order. Used to
# build embedding text: framing every neuron with identical headings would
# inject a constant into every vector and smear the raw sim_* margins that
# evidence-gated abstention and recall depth both read. Metadata slots
# (Evidence receipt, Confidence, Volatility) are audit apparatus, not
# things a user's question is ever "about".
_SEMANTIC_SLOTS: tuple[str, ...] = (
    "Claim", "Entities", "Time scope", "Context", "Future-use", "Likely queries",
)

_MIN_CLAIM_CHARS = 12

# A heading line: slot name at line start, colon, optional value. Matching
# is case-insensitive on the slot name so a model that writes "Time Scope"
# is corrected rather than silently treated as prose.
_HEADING_RE = re.compile(
    r"^(?P<slot>" + "|".join(re.escape(s) for s in CANONICAL_SLOTS) + r")\s*:(?P<rest>.*)$",
    re.IGNORECASE,
)


class EvidenceFrameError(ValueError):
    """A durable-memory write failed the frame contract. Carries every
    problem found, not just the first, so a composing agent can repair the
    whole frame in one retry instead of discovering faults one at a time."""

    def __init__(self, errors: list[str]) -> None:
        assert errors, "EvidenceFrameError requires at least one error"
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class EvidenceFrame:
    """A parsed, validated frame. `slots` preserves canonical order."""
    slots: tuple[tuple[str, str], ...]

    def get(self, slot: str) -> str:
        """Value for a canonical slot name."""
        assert slot in CANONICAL_SLOTS, f"unknown slot {slot!r}"
        return next(v for k, v in self.slots if k == slot)

    @property
    def claim(self) -> str:
        return self.get("Claim")

    @property
    def volatility(self) -> str:
        """Leading controlled token (e.g. 'perishable')."""
        return _lead_token(self.get("Volatility"))

    @property
    def time_scope(self) -> str:
        """Leading controlled token (e.g. 'dated-event')."""
        return _lead_token(self.get("Time scope"))

    @property
    def confidence(self) -> str:
        return _lead_token(self.get("Confidence"))

    def semantic_text(self) -> str:
        """Substance-only projection for embedding (see _SEMANTIC_SLOTS).

        Honest-absence markers are dropped rather than embedded: a corpus
        where most neurons carry the literal token "unknown" would have
        that token contribute to every vector while meaning nothing.
        """
        return " ".join(
            v for k, v in self.slots
            if k in _SEMANTIC_SLOTS and v and not _is_absence(v)
        ).strip()

    def render(self) -> str:
        """Canonical serialization; round-trips through parse_frame."""
        return "\n".join(f"{k}: {v}" for k, v in self.slots)


def _lead_token(value: str) -> str:
    """First whitespace/paren-delimited token of a slot value, casefolded."""
    return re.split(r"[\s(,;]", value.strip(), maxsplit=1)[0].strip().casefold()


def _is_absence(value: str) -> bool:
    return value.strip().casefold().rstrip(".") in _ABSENCE_MARKERS


def _split_slots(text: str) -> list[tuple[str, str]]:
    """Scan text into (canonical_slot, value) pairs in the order found.

    Values may span multiple lines; a line is a new slot only when it
    starts with a canonical heading. Duplicates are preserved so the
    validator can report them rather than silently keeping the last.
    """
    found: list[tuple[str, list[str]]] = []
    for line in (text or "").splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            canonical = next(
                s for s in CANONICAL_SLOTS
                if s.casefold() == m.group("slot").casefold()
            )
            found.append((canonical, [m.group("rest").strip()]))
        elif found and line.strip():
            found[-1][1].append(line.strip())
    return [(slot, " ".join(p for p in parts if p).strip()) for slot, parts in found]


def validate_frame(text: str | None) -> list[str]:
    """Every contract violation in `text`; empty list means valid. Pure."""
    pairs = _split_slots(text or "")
    if not pairs:
        return ["no evidence-frame headings found: durable memory must be "
                "written as a frame, not prose. Required slots in order: "
                + ", ".join(CANONICAL_SLOTS)]

    errors: list[str] = []
    seen = [slot for slot, _ in pairs]

    duplicates = sorted({s for s in seen if seen.count(s) > 1})
    if duplicates:
        errors.append(f"duplicate slots: {', '.join(duplicates)}")

    missing = [s for s in CANONICAL_SLOTS if s not in seen]
    if missing:
        errors.append(f"missing required slots: {', '.join(missing)}")

    # Order check over the slots that ARE present, so a frame with one
    # missing slot reports the omission rather than a confusing order fault.
    present_in_canonical = [s for s in CANONICAL_SLOTS if s in seen]
    first_occurrence = list(dict.fromkeys(seen))
    if first_occurrence != present_in_canonical:
        errors.append(
            f"slots out of canonical order: got {' -> '.join(first_occurrence)}; "
            f"expected {' -> '.join(present_in_canonical)}"
        )

    values = dict(pairs)
    for slot in CANONICAL_SLOTS:
        if slot not in values:
            continue
        value = values[slot].strip()
        if not value:
            errors.append(f"{slot}: empty (required)")
            continue
        vocab = _VOCAB.get(slot)
        if vocab:
            # Controlled slots are settled by membership alone. "unknown"
            # is a REAL time scope (the record requires unstated timing to
            # be distinguishable from a dated event), so the absence rule
            # below must not pre-empt the vocabulary here.
            if _lead_token(value) not in vocab:
                errors.append(
                    f"{slot}: '{value}' must begin with one of {', '.join(vocab)}"
                )
            continue
        if _is_absence(value) and slot not in _ABSENCE_OK:
            errors.append(
                f"{slot}: '{value}' is not an acceptable value — this slot "
                "must carry substance. Do not write a durable memory whose "
                f"{slot.lower()} is unknown."
            )

    claim = values.get("Claim", "").strip()
    if claim and not _is_absence(claim) and len(claim) < _MIN_CLAIM_CHARS:
        errors.append(
            f"Claim: too short ({len(claim)} chars) to reconstruct an answer"
        )

    queries = values.get("Likely queries", "").strip()
    if queries and "?" not in queries:
        errors.append(
            "Likely queries: must contain at least one question phrasing "
            "ending in '?' — a keyword list is not a query"
        )

    return errors


# A fusion result must declare what it DID to the memories it absorbed.
# Without this a merge reads as a fresh observation, and the certificate's
# "janitor fusion loses dates" finding is exactly what a silent merge looks
# like after the fact.
FUSION_RELATIONSHIPS: tuple[str, ...] = (
    "reinforced", "corrected", "narrowed", "superseded", "contradicted",
)


def validate_fusion_frame(text: str | None) -> list[str]:
    """Frame contract PLUS the merge-specific change-relationship rule.

    Used where a write absorbs prior memories (reconsolidation synthesis,
    consolidation merges) rather than recording a fresh observation.
    """
    errors = validate_frame(text)
    if errors:
        return errors
    context = parse_frame(text).get("Context").casefold()
    if not any(rel in context for rel in FUSION_RELATIONSHIPS):
        return [
            "Context: a fused memory must state its relationship to what it "
            f"absorbed — one of {', '.join(FUSION_RELATIONSHIPS)}"
        ]
    return []


def parse_frame(text: str | None) -> EvidenceFrame:
    """Parse and validate; raise EvidenceFrameError with every violation."""
    errors = validate_frame(text)
    if errors:
        raise EvidenceFrameError(errors)
    values = dict(_split_slots(text or ""))
    return EvidenceFrame(tuple((s, values[s]) for s in CANONICAL_SLOTS))


def is_framed(text: str | None) -> bool:
    """True when `text` already satisfies the contract. Never raises."""
    return not validate_frame(text)


def requires_frame(node_type: str | None, abstraction_type: str | None) -> bool:
    """Whether a neuron of this shape must carry an evidence frame.

    Structural scaffolding is exempt by classification: a project
    container or role node asserts nothing that a future agent needs to
    reconstruct. Everything an agent COMPOSES as durable memory is in.
    """
    if (abstraction_type or "").strip().casefold() == "structural":
        return False
    return (node_type or "").strip().casefold() in FRAMED_NODE_TYPES


def enforcement_enabled() -> bool:
    """Whether the contract is enforced on writes.

    This exists for ONE reason: the record requires a LoCoMo before/after
    slice comparing old-style against framed construction, and the control
    arm cannot be produced if framing is unconditional. It defaults to ON
    and is documented as an eval-control switch, not an operator
    convenience — turning it off on a real tenant reopens exactly the
    unstructured-content failure mode this module exists to close.
    """
    from app.config import settings
    return bool(getattr(settings, "mind_evidence_frame_enforced", True))


def enforce(
    text: str | None, *, node_type: str | None, abstraction_type: str | None,
    where: str,
) -> None:
    """Fail closed unless `text` satisfies the contract for this node shape.

    `where` names the construction path in the raised error so a failure
    points at the agent that produced it, not just the validator.
    """
    if not requires_frame(node_type, abstraction_type):
        return
    if not enforcement_enabled():
        return
    errors = validate_frame(text)
    if errors:
        raise EvidenceFrameError(
            [f"[{where}] durable memory ({node_type}) failed the evidence-frame "
             f"contract"] + errors
        )


def build_frame(
    *, claim: str, evidence: str, entities: str = "none",
    time_scope: str = "unknown", context: str = "unknown",
    future_use: str, likely_queries: str,
    confidence: str = "medium", volatility: str = "uncertain",
) -> str:
    """Render a canonical frame from parts, validating before returning.

    Callers that already hold structured fields should use this rather
    than formatting headings by hand, so the slot order and vocabulary
    live in exactly one place.
    """
    frame = "\n".join([
        f"Claim: {claim.strip()}",
        f"Entities: {entities.strip() or 'none'}",
        f"Time scope: {time_scope.strip()}",
        f"Context: {context.strip() or 'unknown'}",
        f"Evidence: {evidence.strip()}",
        f"Future-use: {future_use.strip()}",
        f"Likely queries: {likely_queries.strip()}",
        f"Confidence: {confidence.strip()}",
        f"Volatility: {volatility.strip()}",
    ])
    parse_frame(frame)  # fail closed at the source, not at the write
    return frame


# Prompt fragment shared by every composing agent, so the distiller,
# janitor fusion, and reconsolidation all describe the SAME contract. A
# second hand-written copy of this spec is how the paths drift apart.
FRAME_PROMPT_SPEC = f"""Every durable memory MUST be written as an evidence frame: nine slots, each on its own line, in exactly this order, with no extra headings and no surrounding prose.

{chr(10).join(f"{s}:" for s in CANONICAL_SLOTS)}

Slot rules:
- Claim: the fact itself, declarative, self-contained. A future agent must be able to answer from this line without the original transcript.
- Entities: comma-separated named things this memory is about — people, organizations, projects, tools, places, objects, file paths. Write "none" only when there genuinely are none.
- Time scope: begin with one of {', '.join(TIME_SCOPE_KINDS)}. Add a date in parentheses when known, e.g. "dated-event (2026-07-21)". Never collapse two time-distinct facts into one memory.
- Context: why this matters or how it came about — ONLY if stated or strongly evidenced. Write "unknown" rather than inventing motivation.
- Evidence: the specific source that backs this — session id, error string, command output, user statement, tool receipt. Must be auditable. Never write "unknown" here; a memory you cannot cite must not be written at all.
- Future-use: why a future agent would need this fact.
- Likely queries: natural question phrasings someone might ask to retrieve this. At least one must end with "?".
- Confidence: one of {', '.join(CONFIDENCE_LEVELS)}.
- Volatility: one of {', '.join(VOLATILITY_LEVELS)}. Use "stable" for facts that stay true until explicitly revoked, "perishable" for state that a later observation can legitimately overwrite, "uncertain" when you cannot tell. Never mark a volatile claim stable."""
