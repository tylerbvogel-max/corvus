"""Synaptic homeostasis — the sleep half of plasticity.

(mind-synaptic-downscaling) Corvus had wake without sleep. Attribution
ratchets `avg_utility` UP on reward; the only downward pressure was
`run_decay_audit`, which asks "is this one a warm zombie?" — a per-neuron
value judgment with a ranked kill list. That shape is exactly the thing
Tyler's history names as the recurring failure: pruning that was "too
aggressive or misplaced/bad at targeting."

THE BIOLOGY (Tononi & Cirelli, synaptic homeostasis). Sleep does not decide
which synapses deserve to live. It weakens ALL of them by a common factor.
Relative strengths survive untouched, and the weak ones simply fall under a
fixed threshold and drop out. Selection is EMERGENT from uniform scaling
plus a floor — never decided per synapse. Nothing here asks whether a
memory is valuable, because there is no ground truth for that question and
a graded criterion wired to an actuator is a cobra farm
(mind-recurrence-watch).

WHY IT CANNOT RUN AWAY
  - Proportional scaling cannot invert the ranking, so it can never invent
    a victim order the way a scoring pruner does (property-tested).
  - Anything in use is re-potentiated on the wake side, so the only rows
    that reach the floor are ones nothing touched for many cycles.
  - The action at the bottom is an ACCESSIBILITY SWITCH, not deletion. A
    mistake costs a retrieval, not a memory.
  - The charter and the self-model are exempt by construction, so the
    stratum that would hurt most to lose is structurally out of reach.
  - Decay rides EVIDENCE time (distilled sessions), never wall time, so a
    month of absence does not walk the graph to the floor with nobody
    around to reinforce anything.

FORGETTING IS AN ACCESSIBILITY SWITCH. Silent-engram work (Ryan &
Frankland; Tonegawa) shows the trace survives when natural cues can no
longer reach it — direct stimulation still retrieves it. So a dormant
neuron stays active, stays in the graph, keeps its utility and authority,
and remains fully reachable by direct recall. What stops is COMPILED
delivery: it is no longer written into skills or the charter. It stops
being shouted at every session and goes back to being answered when asked.

SOLE WRITER: this module is the only writer of `neurons.homeostatic_weight`
and `neurons.dormant_at`. Both are DERIVED SIGNAL — the same class as
`centrality` and the pathway counters — recomputed mechanically and outside
the evidence-gated write paths. Deterministic, batch, no LLM, no new
service: a component inside the existing janitor runtime.

WHY NOT avg_utility. The record proposed reusing the delivery columns and
scaling "delivery weight". Both needed correcting against the live code:

  - `delivery_mode` / `delivery_reason` / `delivery_judged_at` are NOT
    free. They are the standing/retrievable charter axis, written by an
    Opus judge in `delivery_mode.classify_delivery` and re-audited every 30
    days. A third value written there would be argued back within one
    cycle, and the two systems would fight over the same column forever.
  - `avg_utility` is EVIDENCE. Attribution writes it, the charter
    promote/demote thresholds read it, and recall scores on it as impact.
    Renormalizing it globally would drag neurons across the authority
    ladder as a side effect — a per-neuron value judgment arriving through
    the back door, which is precisely what this record forbids.

Hence a separate scalar that means one thing and is read for one purpose.

CONSTANTS ARE GUESSED. Every number below was chosen by argument, not
measured, and is labeled as such — same discipline as delivery plasticity.
They are recomputed against the corpus's real usage distribution before the
first live demotion; until then the pass runs in SHADOW mode and writes
nothing.

VOLATILITY IS OBSERVED, NOT OBEYED. The record proposed driving the decay
RATE from the distiller's `volatility` label. The kickoff audit
(~/.corvus-mind/audits/20260802T161110Z-synaptic-downscaling/) measured that
field for the first time and returned: directionally real, statistically
unmeasurable (stable 0/8 rotten vs perishable 6/24, two-sided Fisher
p = 0.30), and structurally applicable to 8.1% of the corpus — 113 of 1,397
neurons carry the label at all, because it lives in the evidence frame and
most classes are unframed. Per the record's own review trigger, the rate is
therefore UNIFORM. The label is still read on every pass and recorded
against each neuron's outcome, so the assumption accumulates the paired
evidence it has never had; when n supports a rate split, the data will be
sitting here. Reading it into the report is not the same as letting it
steer the actuator, and only the second one needs to be earned.
"""

import os
import re
from datetime import datetime, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron
from app.services.mind_corpus import LESSON_TYPES, _log_action

# ── constants (ALL GUESSED — see module docstring) ──────────────────────

# Per-cycle scaling factor. GUESSED. With the floor below this is ~14
# evidence-cycles from full strength to dormancy, which at the observed
# janitor cadence is weeks of a memory going untouched, not days.
DOWNSCALE_RATE = 0.95
# Delivery floor. GUESSED. Chosen at half strength so that crossing it is
# a clearly-decayed signal rather than a rounding accident.
DORMANCY_FLOOR = 0.5
# Ceiling. Use restores to exactly this, so a memory touched even once per
# window never decays — deliberately generous, because the acceptance bar
# is zero false kills and the conservative direction is fewer demotions.
FULL_STRENGTH = 1.0

# Runaway brake. A healthy pass demotes a handful. If a single pass wants
# to quiet more than this, the likely cause is a bad constant or a clock
# fault, not a graph that went cold all at once — so the pass refuses the
# whole batch and reports, rather than demoting the largest set it has
# ever produced. Refusal is logged; nothing is silently truncated.
MAX_DORMANT_PER_RUN = 25

# Authority tier that is human-set and never mechanically touched, and the
# scope that holds the self-model. Exempt rows are never scaled and can
# therefore never go dormant. The record allowed either an unscaled top
# stratum or explicit exemption; explicit is deterministic from cycle one,
# whereas a top quantile is arbitrary while every weight is still 1.0.
EXEMPT_AUTHORITY = ("organizational",)
EXEMPT_DEPARTMENT = ("Assistant",)

_VOLATILITY_RE = re.compile(r"^Volatility:\s*([A-Za-z]+)", re.M)


def apply_enabled() -> bool:
    """Shadow by default. Live demotion is opt-in and stays that way until
    an acceptance window has shown zero false kills on real rows."""
    return os.environ.get("CORVUS_HOMEOSTASIS_APPLY", "").strip().lower() in (
        "1", "true", "yes", "on")


def _exempt_filter():
    """Rows the pass must never touch. Expressed as a filter so the same
    definition drives both the scaling UPDATE and the protection test —
    two hand-written copies would drift.

    `delivery_mode == standing` is the charter wall. The graph has already
    judged, with a countersign-grade Opus verdict, that these facts must be
    present BEFORE the agent knows what the session is about. A standing
    fact is by definition one whose value does not show up as retrieval
    traffic, so scaling it on disuse would condemn it for the exact
    property that earned it standing. Sleep must never be able to quiet
    the charter or the self-model.

    COALESCE IS LOAD-BEARING, not defensive habit. All three columns are
    nullable, and in SQL `NOT (false OR false OR NULL)` is NULL, not true —
    so a plain negation of this filter silently drops every row with a NULL
    `delivery_mode` out of the population. The live probe caught exactly
    that: the first run scaled 32 of 448 eligible neurons and reported
    itself healthy, because three-valued logic had quietly exempted the
    other 416. A pass that is supposed to be GLOBAL cannot be allowed to
    fail that way, so the NULLs are collapsed to a sentinel here, once,
    where both the filter and its negation read it.
    """
    from app.services.delivery_mode import STANDING
    return or_(
        func.coalesce(Neuron.authority_level, "").in_(EXEMPT_AUTHORITY),
        func.coalesce(Neuron.department, "").in_(EXEMPT_DEPARTMENT),
        func.coalesce(Neuron.delivery_mode, "") == STANDING,
    )


def scale_vector(weights: list[float], rate: float = DOWNSCALE_RATE) -> list[float]:
    """The renormalization itself, as a pure function.

    Extracted so the load-bearing property — proportional scaling never
    reorders anything — is testable directly instead of being inferred
    from database state. This is the whole reason the design is safe: a
    scoring pruner invents a new victim order every pass, and this cannot.
    """
    assert 0.0 < rate <= 1.0, f"rate out of bounds: {rate}"
    return [w * rate for w in weights]


def volatility_of(content: str | None) -> str:
    """The distiller's label, or 'unlabeled' for the ~92% of the corpus
    that carries no evidence frame. OBSERVED ONLY — see module docstring;
    this value never reaches the actuator."""
    if not content:
        return "unlabeled"
    found = _VOLATILITY_RE.search(content)
    return found.group(1).lower() if found else "unlabeled"


def dormancy_exclusion_filters() -> list:
    """For consumers of COMPILED delivery (skills, charter).

    Deliberately not applied to recall: dormancy means a memory stops being
    broadcast, not that it stops being knowable. Direct recall must keep
    reaching every dormant row — that is the difference between this and a
    pruner, and it is asserted live in the test suite.
    """
    return [Neuron.dormant_at.is_(None)]


async def run_downscaling(
    db: AsyncSession, *, cycles: int = 1, since: datetime | None = None,
    apply: bool | None = None,
) -> dict:
    """One sleep cycle: renormalize globally, then let the floor select.

    `cycles` is EVIDENCE time (distilled sessions since the last pass); at
    zero the graph has seen nothing new and sleep is skipped along with
    decay. `since` bounds the wake half — neurons accessed after it are
    re-potentiated. With `since` unset (first pass ever) nothing is
    re-potentiated, which is harmless: one step from full strength is
    nowhere near the floor, and `since` exists by the next pass.
    """
    assert cycles >= 0, "cycles cannot be negative"
    live = apply_enabled() if apply is None else apply
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    report: dict = {"mode": "apply" if live else "shadow", "cycles": cycles,
                    "rate": DOWNSCALE_RATE, "floor": DORMANCY_FLOOR,
                    "constants": "GUESSED", "scaled": 0, "repotentiated": 0,
                    "dormant": [], "woke": [], "refused": None}
    if cycles <= 0:
        report["skipped"] = ("no sessions distilled since last run — evidence "
                             "time is frozen, so sleep is too")
        return report

    population = [
        Neuron.is_active.is_(True),
        Neuron.node_type.in_(LESSON_TYPES),
        ~_exempt_filter(),
    ]

    # ── sleep: one global, proportional, set-based multiply ─────────────
    # Done as a single UPDATE rather than a Python loop on purpose. There
    # is no ordering, no limit and no candidate list anywhere in this
    # statement — the absence of a ranking IS the design, and a bounded
    # loop over "the top N" would quietly reintroduce one.
    scaled_rows = 0
    if live:
        result = await db.execute(
            update(Neuron).where(*population).values(
                homeostatic_weight=Neuron.homeostatic_weight * DOWNSCALE_RATE)
        )
        scaled_rows = result.rowcount or 0
    else:
        scaled_rows = len((await db.execute(
            select(Neuron.id).where(*population))).scalars().all())
    report["scaled"] = scaled_rows

    # ── wake: use re-potentiates, unconditionally ───────────────────────
    # Any access at all restores full strength. Deliberately generous: the
    # acceptance bar is zero false kills, so the conservative error is to
    # demote too little. This is the step that makes the floor reachable
    # ONLY by things nothing touched.
    repot = 0
    if since is not None:
        used = [*population, Neuron.last_accessed_at.is_not(None),
                Neuron.last_accessed_at > since]
        if live:
            result = await db.execute(
                update(Neuron).where(*used).values(
                    homeostatic_weight=FULL_STRENGTH,
                    # Re-potentiation is also dishabituation: a dormant row
                    # that got used again comes back on its own, with no
                    # human in the loop. Reversibility is not a promise
                    # here, it is the same statement that demotes.
                    dormant_at=case((Neuron.dormant_at.is_not(None), None),
                                    else_=Neuron.dormant_at)))
            repot = result.rowcount or 0
        else:
            repot = len((await db.execute(
                select(Neuron.id).where(*used))).scalars().all())
    report["repotentiated"] = repot

    # ── the floor selects; nothing ranks ────────────────────────────────
    # In shadow mode the weights were never written, so project the
    # would-be weight rather than reading it back.
    projected = (Neuron.homeostatic_weight if live
                 else Neuron.homeostatic_weight * DOWNSCALE_RATE)
    candidates = list((await db.execute(
        select(Neuron).where(
            *population, Neuron.dormant_at.is_(None),
            projected < DORMANCY_FLOOR,
        ).order_by(Neuron.id)
    )).scalars().all())

    if len(candidates) > MAX_DORMANT_PER_RUN:
        # Refuse the batch whole. Truncating to the first N would demote a
        # set chosen by id, which is a ranking by another name, and would
        # hide the anomaly behind a plausible-looking pass.
        report["refused"] = {
            "reason": "batch exceeds MAX_DORMANT_PER_RUN — suspect a bad "
                      "constant or a clock fault, not a graph gone cold",
            "would_have_demoted": len(candidates),
            "limit": MAX_DORMANT_PER_RUN,
        }
        _log_action("homeostasis.refused", report["refused"])
        if live:
            await db.commit()
        return report

    for n in candidates:  # bounded by MAX_DORMANT_PER_RUN (JPL-2)
        detail = {"neuron_id": n.id, "label": n.label,
                  "weight": round((n.homeostatic_weight or FULL_STRENGTH)
                                  * (1.0 if live else DOWNSCALE_RATE), 4),
                  "invocations": n.invocations,
                  "avg_utility": round(n.avg_utility or 0.5, 3),
                  # Observed, never obeyed. This column is how the
                  # volatility assumption finally accumulates paired data.
                  "volatility": volatility_of(n.content),
                  "mode": report["mode"]}
        if live:
            n.dormant_at = now
            _log_action("homeostasis.dormant", detail)
        report["dormant"].append(detail)

    # ── report the wake side too, so reversibility is visible ───────────
    if live:
        woke = list((await db.execute(
            select(Neuron).where(
                *population, Neuron.dormant_at.is_(None),
                Neuron.homeostatic_weight >= DORMANCY_FLOOR,
                Neuron.last_accessed_at.is_not(None),
            ).order_by(Neuron.id).limit(MAX_DORMANT_PER_RUN)
        )).scalars().all())
        report["woke"] = [{"neuron_id": n.id, "label": n.label}
                          for n in woke if since and n.last_accessed_at
                          and n.last_accessed_at > since]
        await db.commit()

    assert len(report["dormant"]) <= MAX_DORMANT_PER_RUN, "bounded run"
    return report


async def census(db: AsyncSession) -> dict:
    """Read-only distribution of the homeostatic axis.

    The shadow-mode instrument: this is what an acceptance window is read
    from, and what the guessed constants get recomputed against before any
    live demotion is enabled.
    """
    rows = list((await db.execute(
        select(Neuron.homeostatic_weight, Neuron.dormant_at, Neuron.content,
               Neuron.invocations)
        .where(Neuron.is_active.is_(True), Neuron.node_type.in_(LESSON_TYPES))
    )).all())
    # Reported so a reader can check that scaled + exempt accounts for the
    # WHOLE population. That arithmetic is the tripwire for the NULL bug
    # the first live probe found: a filter that silently exempts most of
    # the graph still looks like a healthy pass from its own numbers.
    exempt = (await db.execute(
        select(func.count(Neuron.id)).where(
            Neuron.is_active.is_(True), Neuron.node_type.in_(LESSON_TYPES),
            _exempt_filter())
    )).scalar() or 0
    by_volatility: dict[str, dict] = {}
    for weight, dormant, content, _inv in rows:
        bucket = by_volatility.setdefault(
            volatility_of(content), {"n": 0, "dormant": 0, "weight_sum": 0.0})
        bucket["n"] += 1
        bucket["dormant"] += 1 if dormant else 0
        bucket["weight_sum"] += weight if weight is not None else FULL_STRENGTH
    for bucket in by_volatility.values():
        bucket["mean_weight"] = round(bucket.pop("weight_sum") / bucket["n"], 4)
    return {"population": len(rows), "exempt": exempt,
            "dormant": sum(1 for _, d, _, _ in rows if d),
            "by_volatility": by_volatility}
