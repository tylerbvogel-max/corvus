"""Step 04 live probe, stage 2 — planted-pair proof through the production
scan → classify → resolve pipeline.

Stage 1 (step04_live_janitor_probe.py, receipt probe-receipt.json) ran a
full janitor pass under the new code: janitor fired on the organic smoke
corpus (consolidation proposal + the sunset/sunrise ambiguous pair routed to
review, zero supersessions) — but the planted pairs never reached the
classifier because the conflict scan judges only the top-40 most-similar
contradiction-zone pairs and the plants sat at 0.696/0.729 in a 58-neuron
corpus. That truncation is a latent coverage gap in its own right (noted in
the verdict doc), not a defect in the durability gate.

This stage isolates the plants in their own department and runs the SAME
production pieces run_staleness runs — scan_contradictions (scoped) then
_resolve_contradiction over the open findings — so the classifier and
resolver both genuinely execute against the live database.

Housekeeping disclosed in the receipt: rows with
source_origin='step04-probe' from a previous probe attempt are deleted
first (probe scaffolding in a throwaway smoke DB, not LoCoMo data).
Receipt: probe2-receipt.json (stage-1 receipt left untouched).
"""
import asyncio
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "backend"))

ART = os.path.expanduser(
    "~/.corvus-mind/evals/locomo/20260726T155942Z-step04-supersession-forensics")
JANITOR_DIR = os.path.join(ART, "live-janitor-probe")
DEPT = "ProbeIsolation"

PLANTS = [
    ("probe: Riley heading to Osaka next month",
     "Riley said they are heading to Osaka next month for a conference.",
     "perishable-older"),
    ("probe: Riley cancelled the Osaka trip",
     "Riley cancelled the planned Osaka trip after the conference moved online.",
     "perishable-newer"),
    ("probe: Riley works as a nurse",
     "Riley works as a nurse at the county hospital.",
     "durable-older"),
    ("probe: Riley works as a firefighter",
     "Riley works as a firefighter with the county fire department.",
     "durable-newer"),
]


async def main():
    assert os.environ.get("TENANT_ID") == "corvus-locomo", \
        "refusing to run outside the corvus-locomo eval tenant"

    from datetime import datetime, timedelta

    from sqlalchemy import delete, select

    import app.services.mind_janitors as mj
    from app.database import async_session
    from app.models import IntegrityFinding, Neuron
    from app.services.actions.init_registry import init_actions_registry
    from app.services.embedding_service import embed_text
    from app.services.integrity.conflict_monitor import scan_contradictions

    init_actions_registry()   # standalone scripts must register edge.link etc.
    mj.EPISODE_DIR = JANITOR_DIR
    mj.ACTIONS_LOG = os.path.join(JANITOR_DIR, "janitor-actions.jsonl")

    base = datetime.utcnow() - timedelta(days=2)
    async with async_session() as db:
        stale = (await db.execute(
            select(Neuron.id).where(Neuron.source_origin == "step04-probe")
        )).scalars().all()
        stale_findings = []
        if stale:
            await db.execute(delete(Neuron).where(Neuron.id.in_(stale)))
            for f in (await db.execute(
                select(IntegrityFinding).where(
                    IntegrityFinding.finding_type == "contradiction")
            )).scalars().all():
                pair = set(json.loads(f.neuron_ids_json or "[]"))
                if pair & set(stale):
                    stale_findings.append(f.id)
                    await db.delete(f)
        planted: dict[str, Neuron] = {}
        for i, (label, content, role) in enumerate(PLANTS):
            n = Neuron(
                label=label, content=content, summary=content,
                department=DEPT, layer=3, node_type="lesson",
                source_origin="step04-probe", is_active=True,
                invocations=1, avg_utility=0.5,
                embedding=json.dumps(embed_text(f"{label}. {content}")),
                created_at=base + timedelta(hours=i),
            )
            db.add(n)
            planted[role] = n
        await db.commit()
        for n in planted.values():
            await db.refresh(n)
        ids = {role: n.id for role, n in planted.items()}
        print("removed stale probe rows:", stale)
        print("planted:", json.dumps(ids))

        # the two production pieces run_staleness runs, scan scoped to the
        # planted department so the classifier definitely judges the pairs
        await scan_contradictions(
            db, scope=f"department:{DEPT}", max_pairs=10,
            initiated_by="step04_probe2",
        )
        findings = (await db.execute(
            select(IntegrityFinding).where(
                IntegrityFinding.finding_type == "contradiction",
                IntegrityFinding.status == "open",
                IntegrityFinding.resolution.is_(None),
            )
        )).scalars().all()
        resolutions = []
        for finding in findings:
            out = await mj._resolve_contradiction(db, finding)
            if out is not None:
                resolutions.append(out)
        await db.commit()

    async with async_session() as db:
        rows = {n.id: n for n in (await db.execute(
            select(Neuron).where(Neuron.id.in_(list(ids.values())))
        )).scalars().all()}
        per_old = rows[ids["perishable-older"]]
        dur_old = rows[ids["durable-older"]]
        probe_findings = []
        for f in (await db.execute(
            select(IntegrityFinding).where(
                IntegrityFinding.finding_type == "contradiction")
        )).scalars().all():
            pair = set(json.loads(f.neuron_ids_json or "[]"))
            if pair & set(ids.values()):
                det = json.loads(f.detail_json or "{}")
                probe_findings.append({
                    "id": f.id, "status": f.status, "resolution": f.resolution,
                    "classification": det.get("classification"),
                    "resolution_hint": det.get("resolution_hint"),
                    "reasoning": (det.get("llm_reasoning") or "")[:200],
                    "pair": sorted(pair),
                })

    checks = {
        "perishable_older_superseded":
            per_old.superseded_by == ids["perishable-newer"],
        "perishable_older_demoted": (per_old.avg_utility or 0) < 0.5,
        "durable_older_untouched":
            dur_old.superseded_by is None
            and (dur_old.avg_utility or 0) == 0.5,
        "durable_finding_in_review": any(
            f["resolution"] == "needs_review" and f["status"] == "open"
            and set(f["pair"]) == {ids["durable-older"], ids["durable-newer"]}
            for f in probe_findings),
    }
    receipt = {
        "removed_stale_probe_rows": stale,
        "removed_stale_probe_findings": stale_findings,
        "planted_ids": ids,
        "checks": checks,
        "probe_findings": probe_findings,
        "resolutions": resolutions,
    }
    with open(os.path.join(JANITOR_DIR, "probe2-receipt.json"), "w") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(json.dumps(receipt, indent=1, default=str))
    ok = all(checks.values())
    print("PROBE2", "GREEN" if ok else "RED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
