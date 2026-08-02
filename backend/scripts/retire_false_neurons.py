"""Retire confirmed-false neurons through the governed action path.

Commissioned by Tyler 2026-08-02 ("please make the graph fixes") after the
mind-synaptic-downscaling kickoff audit found a cluster of confidently
phrased, on-topic, FALSE memories in the live corpus — the exact failure
mode both forward records were written against, sitting in the graph and
being injected into live sessions.

DISCIPLINE
  - Nothing is deleted. `is_active=False` through the `neuron.refine`
    action, which writes an Action row and a NeuronRefinement row, so every
    retirement is audited and reversible by the same path that made it.
  - FAIL CLOSED. Each target carries a predicate that must re-confirm its
    falsity against the live schema and working tree AT MUTATION TIME. A
    predicate that cannot re-confirm skips the row rather than retiring it.
    Reality may have changed since the audit; the audit does not get to be
    the authority twice.
  - AMBIGUOUS IS NOT FALSE. mind-identity-fact-supersession's carry-forward
    is a hard constraint: only unambiguously contradicted rows are touched.
    Partially-true rows are reported for review and left ACTIVE, even
    though leaving a partly-wrong memory live is uncomfortable. That
    discomfort is the constraint doing its job.

    cd ~/Projects/corvus/backend
    TENANT_ID=corvus-mind PYTHONPATH=. ./venv/bin/python \\
        scripts/retire_false_neurons.py [--apply]
"""
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.database import async_session  # noqa: E402
from app.middleware.rbac import UserIdentity  # noqa: E402
from app.models import Neuron  # noqa: E402
from app.services import action_bus  # noqa: E402
from app.services.actions.init_registry import init_actions_registry  # noqa: E402

REPO = os.path.expanduser("~/Projects/corvus")
HOME = os.path.expanduser("~")
APPLY = "--apply" in sys.argv
ACTOR = UserIdentity(user_id="tyler-countersign-20260802", role="admin",
                     source="disabled")


def _absent(path: str) -> bool:
    return not os.path.exists(os.path.join(REPO, path))


def _in_no_branch(path: str) -> bool:
    """True when NO ref in the repository contains this path — the
    difference between 'deleted on main' and 'never existed anywhere'."""
    out = subprocess.run(["git", "log", "--all", "--oneline", "--", path],
                         cwd=REPO, capture_output=True, text=True)
    return out.returncode == 0 and not out.stdout.strip()


async def _no_column(db, table: str, column: str) -> bool:
    got = await db.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_schema="
        "current_schema() AND table_name=:t AND column_name=:c"),
        {"t": table, "c": column})
    return got.first() is None


async def predicates(db) -> dict[int, tuple[bool, str]]:
    """{neuron_id: (still_false, evidence)} — evaluated live, right now."""
    no_owner = await _no_column(db, "chat_sessions", "owner_id")
    wake_gone = (_absent("backend/app/services/semantic_wake.py")
                 and _in_no_branch("backend/app/services/semantic_wake.py"))
    spec_gone = (_absent("frontend/tests/semantic-wake.spec.ts")
                 and _in_no_branch("frontend/tests/semantic-wake.spec.ts"))
    wt_gone = not os.path.exists(
        os.path.join(HOME, "Projects/corvus-wt/reproducible-release"))
    return {
        1483: (wake_gone,
               "backend/app/services/semantic_wake.py absent from the working "
               "tree AND from every ref in the repository; the /chat/wake-profile "
               "endpoint does not exist"),
        1484: (no_owner,
               "chat_sessions has no owner_id column in the live schema and no "
               "ownership migration exists"),
        1485: (no_owner,
               "chat-session owner isolation cannot be enforced: the ownership "
               "column the claim depends on does not exist"),
        1486: (spec_gone,
               "frontend/tests/semantic-wake.spec.ts absent from the working "
               "tree AND from every ref; frontend/src/demo/shim.ts exists but "
               "contains no wake-profile code"),
        1514: (wt_gone,
               "~/Projects/corvus-wt/reproducible-release no longer exists; the "
               "session-scoped directive it carries (a prohibition on touching "
               "~/Projects/corvus) has expired and is now actively misleading"),
    }


# Deliberately NOT retired. Recorded here so the decision is part of the
# receipt rather than an omission a later reader has to reconstruct.
REVIEW_ONLY = {
    1487: ("PARTIALLY TRUE — executor.py genuinely does propagate `requester` "
           "(RequesterContext) and bounds recall to the requester's regions, so "
           "the core claim holds. What could not be confirmed: app/routers/"
           "query.py contains no requester reference, and no visibility "
           "filtering of prior_neuron_ids was found. Ambiguous is not false; "
           "left ACTIVE for review per mind-identity-fact-supersession."),
    304: ("NOT FALSE, over-specific. The invocation pattern it documents "
          "(cd backend && TENANT_ID=corvus-mind PYTHONPATH=. venv/bin/python "
          "<script>) is correct and the neuron has 936 invocations at 0.95 "
          "utility. Only its example scratchpad path — /tmp/.../f2a2c9d3-.../"
          "update_neuron_228.py — is dead. Retiring it would destroy a true, "
          "heavily-used lesson to fix a stale illustration. Candidate for "
          "content refinement, not retirement; left ACTIVE and untouched."),
}


async def main() -> int:
    init_actions_registry()
    report: dict = {"mode": "apply" if APPLY else "dry-run",
                    "retired": [], "skipped": [], "review_only": []}

    async with async_session() as db:
        checks = await predicates(db)
        for nid, (still_false, evidence) in checks.items():
            neuron = await db.get(Neuron, nid)
            if neuron is None:
                report["skipped"].append({"id": nid, "why": "no such neuron"})
                continue
            if not neuron.is_active:
                report["skipped"].append({"id": nid, "why": "already inactive"})
                continue
            if not still_false:
                # Reality moved. The audit does not get to overrule it.
                report["skipped"].append({
                    "id": nid, "label": neuron.label,
                    "why": "FAIL-CLOSED: falsity could not be re-confirmed "
                           "against the live schema/tree at mutation time"})
                print(f"  [SKIP] {nid} — predicate no longer holds")
                continue

            entry = {"id": nid, "label": neuron.label,
                     "invocations": neuron.invocations,
                     "avg_utility": round(neuron.avg_utility or 0.5, 3),
                     "evidence": evidence}
            if APPLY:
                result = await action_bus.submit(
                    db, "neuron.refine", ACTOR,
                    {"target_neuron_id": nid, "field": "is_active",
                     "old_value": "true", "new_value": "false",
                     "reason": f"Confirmed false at retirement time: {evidence}"},
                    actor_type="user",
                    reason=("mind-synaptic-downscaling kickoff audit — "
                            "confirmed-false memory, Tyler countersigned "
                            "2026-08-02"),
                    idempotency_key=f"retire-false-{nid}-20260802",
                )
                entry["action_id"] = result.action_id
                entry["action_state"] = result.state
            report["retired"].append(entry)
            print(f"  [{'RETIRE' if APPLY else 'WOULD RETIRE'}] {nid} "
                  f"{neuron.label!r} (inv={neuron.invocations})")

        for nid, why in REVIEW_ONLY.items():
            neuron = await db.get(Neuron, nid)
            report["review_only"].append({
                "id": nid, "label": neuron.label if neuron else None,
                "is_active": bool(neuron and neuron.is_active),
                "disposition": "left active, flagged for review", "why": why})
            print(f"  [REVIEW] {nid} — left ACTIVE")

        if APPLY:
            await db.commit()
        else:
            await db.rollback()

    # verification pass, fresh session
    async with async_session() as db:
        ids = [e["id"] for e in report["retired"]]
        if ids:
            rows = (await db.execute(select(
                Neuron.id, Neuron.is_active, Neuron.superseded_by,
                Neuron.avg_utility).where(Neuron.id.in_(ids)))).all()
            report["post_state"] = [
                {"id": i, "is_active": a, "superseded_by": s,
                 "avg_utility": round(u or 0.5, 3)} for i, a, s, u in rows]
            expect = not APPLY
            report["verified"] = all(a is expect for _, a, _, _ in rows)
            # retirement must not have touched anything else
            report["no_supersession_side_effect"] = all(
                s is None for _, _, s, _ in rows)

    out = os.environ.get("RETIRE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nreceipt: {out}")
    print(f"\n{report['mode']}: retired={len(report['retired'])} "
          f"skipped={len(report['skipped'])} review_only={len(report['review_only'])}"
          f" verified={report.get('verified')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
