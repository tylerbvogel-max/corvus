"""Step 04 live probe — plant a perishable pair and a durable pair in the
smoke corpus, run a real janitor pass under the new code, and verify both
directions against the live database.

Why this exists: the 4-session smoke never reaches janitor cadence (first
firing is session 8 of the 200-per-week profile), so neither smoke exercises
the staleness lane. This probe is the planted-pair regression run LIVE:
same tenant env as the harness, real scan → classify → resolve pipeline.

Planted rows are disclosed in the receipts by id and label; the smoke DB is
a throwaway artifact of the smoke run. corvus_locomo only. Janitor logs are
written to a NEW subdirectory of the step04 forensics artifact dir — no
existing evidence file is touched.

Usage (wrapper env required, same as run_locomo.sh):
  set -a; source deploy/memory-tenant.env; set +a
  TENANT_ID=corvus-locomo backend/venv/bin/python \
      eval/locomo/analysis/step04_live_janitor_probe.py
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

PLANTS = [
    # perishable pair: genuine state update — MUST still be superseded
    ("probe: Riley heading to Osaka next month",
     "Riley said they are heading to Osaka next month for a conference.",
     "perishable-older"),
    ("probe: Riley cancelled the Osaka trip",
     "Riley cancelled the planned Osaka trip after the conference moved online.",
     "perishable-newer"),
    # durable pair: standing conflict — MUST survive and land in review
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
    os.makedirs(JANITOR_DIR, exist_ok=True)

    from datetime import datetime, timedelta

    from sqlalchemy import select

    import app.services.mind_janitors as mj
    from app.database import async_session
    from app.models import IntegrityFinding, Neuron
    from app.services.embedding_service import embed_text

    mj.EPISODE_DIR = JANITOR_DIR
    mj.ACTIONS_LOG = os.path.join(JANITOR_DIR, "janitor-actions.jsonl")

    # fail fast if a planted pair would miss the contradiction zone —
    # the scan prefilter only classifies pairs in [sim_min, sim_max]
    from app.config import settings as cfg

    def _cos(u, v):
        dot = sum(a * b for a, b in zip(u, v))
        nu = sum(a * a for a in u) ** 0.5
        nv = sum(b * b for b in v) ** 0.5
        return dot / (nu * nv)

    vecs = [embed_text(f"{lb}. {ct}") for lb, ct, _ in PLANTS]
    for i, j, name in ((0, 1, "perishable"), (2, 3, "durable")):
        sim = _cos(vecs[i], vecs[j])
        print(f"planted {name} pair sim: {sim:.3f} "
              f"(zone {cfg.integrity_conflict_sim_min}-"
              f"{cfg.integrity_conflict_sim_max})")
        assert cfg.integrity_conflict_sim_min <= sim <= \
            cfg.integrity_conflict_sim_max, \
            f"{name} pair sim {sim:.3f} outside contradiction zone — " \
            "reword the planted texts"

    base = datetime.utcnow() - timedelta(days=2)
    planted: dict[str, Neuron] = {}
    async with async_session() as db:
        for i, (label, content, role) in enumerate(PLANTS):
            n = Neuron(
                label=label, content=content, summary=content,
                department="User", layer=3, node_type="lesson",
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
        print("planted:", json.dumps(ids))

        report = await mj.run_janitors(db)

    # verify both directions against the live DB in a fresh session
    async with async_session() as db:
        rows = {n.id: n for n in (await db.execute(
            select(Neuron).where(Neuron.id.in_(list(ids.values())))
        )).scalars().all()}
        per_old = rows[ids["perishable-older"]]
        dur_old = rows[ids["durable-older"]]
        findings = (await db.execute(
            select(IntegrityFinding).where(
                IntegrityFinding.finding_type == "contradiction")
        )).scalars().all()

    checks = {
        "perishable_older_superseded":
            per_old.superseded_by == ids["perishable-newer"],
        "perishable_older_demoted": (per_old.avg_utility or 0) < 0.5,
        "durable_older_untouched":
            dur_old.superseded_by is None
            and (dur_old.avg_utility or 0) == 0.5,
        "durable_finding_in_review": any(
            f.resolution == "needs_review" and f.status == "open"
            and set(json.loads(f.neuron_ids_json or "[]"))
            == {ids["durable-older"], ids["durable-newer"]}
            for f in findings),
        "janitor_actions_fired": os.path.exists(mj.ACTIONS_LOG),
    }
    staleness = report.get("staleness", {})
    receipt = {
        "planted_ids": ids,
        "checks": checks,
        "staleness_report": staleness,
        "janitor_report_keys": sorted(report.keys()),
    }
    with open(os.path.join(JANITOR_DIR, "probe-receipt.json"), "w") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(json.dumps({"checks": checks,
                      "staleness_superseded": len(staleness.get("superseded", [])),
                      "staleness_review": len(staleness.get("review_flagged", [])),
                      }, indent=1))
    ok = all(checks.values())
    print("PROBE", "GREEN" if ok else "RED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
