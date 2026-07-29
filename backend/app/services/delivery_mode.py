"""The delivery axis: standing policy vs retrievable fact.

`authority_level` answers "how much does the graph trust this?".  It was
also, accidentally, answering "should this be injected into every session
forever?" — so any fact that earned guidance tier silently bought
permanent presence, and a 6000-char charter filled with venv paths and
port numbers started evicting actual policy (measured: charter_overflow
fired 13 times).

Those are independent properties.  This module supplies the second one
and leaves the authority ladder untouched, so attribution-driven
promotion keeps working exactly as designed.

Membership stays EARNED and RE-AUDITABLE, which is the whole point of not
replacing it with a hand-maintained list: every verdict is judged from
the neuron's own content, written with its reason, logged as a janitor
action, and re-judged once it goes stale.  A human can overrule any
verdict, and the next audit will argue back.

Demotion here is a DELIVERY change and never a retirement: the neuron
keeps its authority level, its utility, and its full retrievability. It
stops being shouted at every session and goes back to being answered when
asked.
"""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron
from app.services.mind_janitors import LESSON_TYPES, _log_action

STANDING = "standing"
RETRIEVABLE = "retrievable"
VERDICTS = (STANDING, RETRIEVABLE)

JUDGE_MODEL = "opus"
# Graph mutations get the quality-first model: this runs rarely and
# decides what occupies every session's context window from then on.
MAX_BATCH = 12
MAX_BATCHES = 20
RE_AUDIT_DAYS = 30

JUDGE_SYSTEM_PROMPT = """You decide HOW a remembered fact should be DELIVERED to a coding agent. You are not judging whether it is true, useful, or trustworthy — assume every input is all three. You are judging one thing only: must it be present before the agent knows what the session is about, or is it better answered when something asks for it?

"standing" — a rule, policy, preference, or constraint that should shape behavior BEFORE the task is known. If the agent only learns it after already acting, the damage is done. Standing items are phrased as how to behave, what to prefer, what never to do.
Examples: "Never push this repo to the public remote." "Prefer the quality model for graph mutations." "When trust and velocity conflict, trust wins." "Always verify against the running system before claiming done."

"retrievable" — a specific fact that answers a specific question: a location, path, port, filename, command, schema, database name, version, or identifier. It is ideal to look up on demand because the query that needs it is obvious and specific. Knowing it in advance prevents nothing; not knowing it costs one lookup.
Examples: "The backend venv is at backend/venv." "Corvus-mind runs on port 8005." "The recall router is app/routers/recall.py." "Tests need PYTHONPATH=. from the backend dir."

The distinguishing test: if the agent had NOT been told this in advance, would it make a MISTAKE, or would it just LOOK SOMETHING UP? Mistake means standing. Lookup means retrievable.

Be strict. Standing delivery is a scarce budget spent on every future session. A fact that merely feels important is still retrievable if its absence only costs a lookup. When genuinely torn, answer "retrievable" — that channel still delivers it, just on demand.

Respond with ONLY a JSON array, no markdown fences:
[{"item": 1, "verdict": "standing|retrievable", "reason": "<one short clause naming the deciding property>"}]"""


def _block(index: int, neuron: Neuron) -> str:
    body = (neuron.summary or neuron.content or "").strip().replace("\n", " ")
    return (f"Item {index} [scope: {neuron.department or 'global'}]: "
            f"{neuron.label} — {body[:400]}")


def _parse(text: str, count: int) -> dict[int, tuple[str, str]]:
    """{item_index: (verdict, reason)} for well-formed entries only.

    An unparseable or invalid verdict is simply absent, which leaves that
    neuron unjudged so the next cycle retries it — never defaulted to
    standing, because a parse failure must not buy permanent injection."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        items = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    out: dict[int, tuple[str, str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("item", 0))
        except (TypeError, ValueError):
            continue
        verdict = str(item.get("verdict", ""))
        if not (1 <= n <= count) or verdict not in VERDICTS:
            continue
        out[n] = (verdict, str(item.get("reason", ""))[:400])
    return out


async def _judge(batch: list[Neuron]) -> dict[int, tuple[str, str]]:
    from app.services.llm_provider import llm_chat

    assert 0 < len(batch) <= MAX_BATCH, "judge batch out of bounds"
    reply = await llm_chat(
        system_prompt=JUDGE_SYSTEM_PROMPT,
        user_message="\n\n".join(_block(i, n) for i, n in enumerate(batch, 1)),
        max_tokens=1200, model=JUDGE_MODEL, timeout=240,
        workload="delivery_classification",
    )
    return _parse(reply.get("text", ""), len(batch))


def charter_eligible_filters() -> list:
    """Everything except the delivery axis itself — the trust, identity,
    and scope walls the charter has always enforced. Kept here so the
    classifier judges exactly the population the charter draws from."""
    from app.services.reference_class import reference_exclusion_filters
    from app.services.skill_compiler import CHARTER_TIERS
    return [
        Neuron.is_active.is_(True),
        Neuron.node_type.in_(LESSON_TYPES),
        Neuron.superseded_by.is_(None),
        Neuron.authority_level.in_(CHARTER_TIERS),
        Neuron.department != "Assistant",
        *reference_exclusion_filters(),
    ]


async def pending_classification(db: AsyncSession) -> list[Neuron]:
    """Charter-eligible neurons that are unjudged or whose verdict is due
    for re-audit. Re-auditing is what keeps membership arguable rather
    than frozen."""
    stale_before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=RE_AUDIT_DAYS)
    return list((await db.execute(
        select(Neuron).where(
            *charter_eligible_filters(),
            or_(Neuron.delivery_mode.is_(None),
                Neuron.delivery_judged_at.is_(None),
                Neuron.delivery_judged_at < stale_before),
        ).order_by(Neuron.id)
    )).scalars().all())


async def classify_delivery(db: AsyncSession, limit: int = MAX_BATCH * MAX_BATCHES) -> dict:
    """Judge pending neurons and persist their delivery verdicts.

    Returns counts plus every verdict CHANGE, so a recompile can report
    exactly which neurons left the charter and why."""
    assert limit > 0, "limit must be positive"
    pending = (await pending_classification(db))[:limit]
    counts = {"judged": 0, STANDING: 0, RETRIEVABLE: 0, "unjudged": 0}
    changes: list[dict] = []
    if not pending:
        return {**counts, "pending": 0, "changes": changes}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    batches = [pending[i:i + MAX_BATCH] for i in range(0, len(pending), MAX_BATCH)]
    assert len(batches) <= MAX_BATCHES, "too many judge batches"
    for batch in batches:
        verdicts = await _judge(batch)
        for index, neuron in enumerate(batch, 1):
            if index not in verdicts:
                counts["unjudged"] += 1
                continue
            verdict, reason = verdicts[index]
            previous = neuron.delivery_mode
            neuron.delivery_mode = verdict
            neuron.delivery_reason = reason
            neuron.delivery_judged_at = now
            counts["judged"] += 1
            counts[verdict] += 1
            if previous != verdict:
                changes.append({"neuron_id": neuron.id, "label": neuron.label,
                                "from": previous, "to": verdict,
                                "reason": reason})
            _log_action("delivery.classify", {
                "neuron_id": neuron.id, "label": neuron.label,
                "verdict": verdict, "previous": previous, "reason": reason,
                # Delivery is orthogonal to trust; recording the authority
                # it KEPT is the receipt that this was not a demotion.
                "authority_level": neuron.authority_level,
            })
    await db.commit()
    return {**counts, "pending": len(pending), "changes": changes}
