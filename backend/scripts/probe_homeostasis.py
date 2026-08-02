"""Live probe for synaptic homeostasis (mind-synaptic-downscaling).

Three sections, and the split between them is the whole safety design:

  A. SHADOW CENSUS against the REAL corpus. Read-only by construction —
     `apply=False` executes no UPDATE at all — and asserted read-only here
     before and after. This is the acceptance-window instrument: what would
     the pass do today, and to whom.

  B. PLANTED PROBES against a THROWAWAY database, never the live one.
     Exercises decay to dormancy, all three exemptions, dishabituation,
     direct-recall reachability of a dormant row, and the runaway brake.

  C. LIVE-CORPUS VERIFICATION that section B changed nothing.

WHY B CANNOT USE A ROLLED-BACK TRANSACTION. The first version of this probe
planted rows in `corvus_mind` inside a transaction it intended to roll back.
`run_downscaling(apply=True)` commits internally — as every janitor pass
does — so the rollback had nothing left to undo and 36 planted rows landed
in live memory (cleaned; see the record's disclosures). A pass that commits
cannot be contained by its caller's transaction, so containment has to come
from the TARGET instead. Hence a separate database and a name guard that
refuses anything not explicitly disposable.

    cd ~/Projects/corvus/backend
    sudo -u postgres psql -c \\
      'CREATE DATABASE corvus_test_homeostasis OWNER yggdrasil'
    TENANT_ID=corvus-mind PYTHONPATH=. \\
      DATABASE_URL=postgresql+asyncpg://yggdrasil@localhost/corvus_test_homeostasis \\
      ./venv/bin/alembic upgrade head
    TENANT_ID=corvus-mind PYTHONPATH=. ./venv/bin/python scripts/probe_homeostasis.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker, create_async_engine,
)

from app.database import async_session  # noqa: E402
from app.models import Neuron  # noqa: E402
from app.services import synaptic_homeostasis as sh  # noqa: E402

PROBE_DB = os.environ.get("PROBE_DB", "corvus_test_homeostasis").strip()
DISPOSABLE_PREFIXES = ("corvus_test_", "corvus_migration_")
NOW = datetime.now(timezone.utc).replace(tzinfo=None)
SINCE = NOW - timedelta(days=1)
PLANT_ORIGIN = "homeostasis-probe"

results: dict = {"probe": "synaptic-homeostasis", "ran_at": NOW.isoformat(),
                 "probe_db": PROBE_DB,
                 "constants": {"rate": sh.DOWNSCALE_RATE,
                               "floor": sh.DORMANCY_FLOOR,
                               "max_dormant_per_run": sh.MAX_DORMANT_PER_RUN,
                               "status": "GUESSED"}}


def check(name: str, condition: bool, detail: str = "") -> bool:
    results.setdefault("checks", []).append(
        {"name": name, "pass": bool(condition), "detail": detail})
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    return bool(condition)


def _plant(db, nid: int, *, weight: float, authority="informational",
           department="Engineering", delivery=None, dormant=None,
           accessed=None, volatility="perishable"):
    db.add(Neuron(
        id=nid, layer=3, node_type="lesson", label=f"probe-{abs(nid)}",
        content=f"Claim: probe row {nid}\nVolatility: {volatility}",
        summary="homeostasis probe", department=department,
        invocations=5, avg_utility=0.5, is_active=True,
        source_type="probe", source_origin=PLANT_ORIGIN,
        authority_level=authority, delivery_mode=delivery,
        homeostatic_weight=weight, dormant_at=dormant, last_accessed_at=accessed,
    ))


async def section_a() -> None:
    """Shadow census on the untouched live corpus. Read-only."""
    print("\nA. SHADOW CENSUS (live corpus, no plants, apply=False)")
    async with async_session() as db:
        before = (await db.execute(select(
            func.count(Neuron.id),
            func.count(Neuron.id).filter(Neuron.homeostatic_weight != 1.0),
            func.count(Neuron.dormant_at)))).one()
        baseline = await sh.census(db)
        shadow = await sh.run_downscaling(db, cycles=1, since=SINCE, apply=False)
        after = (await db.execute(select(
            func.count(Neuron.id),
            func.count(Neuron.id).filter(Neuron.homeostatic_weight != 1.0),
            func.count(Neuron.dormant_at)))).one()

    results["baseline_census"] = baseline
    results["shadow_pass"] = {k: v for k, v in shadow.items() if k != "dormant"}
    results["shadow_pass"]["dormant_count"] = len(shadow["dormant"])
    results["shadow_would_demote"] = shadow["dormant"]
    print(f"  population={baseline['population']} dormant={baseline['dormant']}")
    print("  volatility coverage: " + json.dumps(
        {k: v["n"] for k, v in sorted(baseline["by_volatility"].items())}))
    print(f"  shadow: scaled={shadow['scaled']} "
          f"repotentiated={shadow['repotentiated']} "
          f"would_demote={len(shadow['dormant'])}")
    check("shadow pass mutates nothing", before == after,
          f"row/weight/dormant counts identical: {before}")
    check("shadow scales the WHOLE eligible population",
          shadow["scaled"] == baseline["population"] - _exempt_count(baseline),
          f"scaled {shadow['scaled']} of {baseline['population']} active lessons")
    check("first pass demotes nobody", not shadow["dormant"],
          "corpus is at full strength; nothing can be below the floor")


def _exempt_count(baseline: dict) -> int:
    return baseline.get("exempt", 0)


async def section_b(session_factory) -> None:
    print(f"\nB. PLANTED PROBES (throwaway db: {PROBE_DB})")
    below = sh.DORMANCY_FLOOR / sh.DOWNSCALE_RATE * 0.99
    async with session_factory() as db:
        await db.execute(Neuron.__table__.delete())
        await db.commit()
        _plant(db, -1, weight=below)                                  # decays
        _plant(db, -2, weight=0.9)                                    # survives
        _plant(db, -3, weight=below, authority="organizational")      # exempt
        _plant(db, -4, weight=below, department="Assistant")          # exempt
        _plant(db, -5, weight=below, delivery="standing")             # exempt
        _plant(db, -6, weight=0.1, dormant=NOW - timedelta(days=3),
               accessed=NOW - timedelta(hours=1), volatility="stable")
        await db.commit()

        report = await sh.run_downscaling(db, cycles=1, since=SINCE, apply=True)
        demoted = {d["neuron_id"] for d in report["dormant"]}
        rows = {n.id: n for n in (await db.execute(
            select(Neuron))).scalars().all()}
        results["planted_pass"] = {k: v for k, v in report.items()}

        check("sub-floor neuron goes dormant",
              -1 in demoted and rows[-1].dormant_at is not None,
              f"weight {rows[-1].homeostatic_weight:.4f} < {sh.DORMANCY_FLOOR}")
        check("above-floor neuron survives",
              -2 not in demoted and rows[-2].dormant_at is None,
              f"weight {rows[-2].homeostatic_weight:.4f}")
        for nid, axis in ((-3, "organizational authority"),
                          (-4, "Assistant self-model"),
                          (-5, "standing charter delivery")):
            check(f"exempt: {axis} refused demotion",
                  nid not in demoted and rows[nid].dormant_at is None
                  and abs(rows[nid].homeostatic_weight - below) < 1e-9,
                  f"unscaled at {rows[nid].homeostatic_weight:.4f}")
        check("dishabituation: used dormant row wakes with no human",
              rows[-6].dormant_at is None
              and rows[-6].homeostatic_weight == sh.FULL_STRENGTH,
              f"weight restored to {rows[-6].homeostatic_weight}")
        check("volatility recorded against every outcome",
              bool(report["dormant"])
              and all(d.get("volatility") == "perishable"
                      for d in report["dormant"]),
              f"labels {[d['volatility'] for d in report['dormant']]}")

        d1 = rows[-1]
        check("dormant row stays active and unretired",
              d1.is_active and d1.superseded_by is None)
        check("dormant row keeps its utility and authority",
              d1.avg_utility == 0.5 and d1.authority_level == "informational")
        direct = (await db.execute(select(Neuron).where(
            Neuron.id == -1, Neuron.is_active.is_(True)))).scalar_one_or_none()
        check("direct recall still reaches the dormant row",
              direct is not None and direct.dormant_at is not None)
        compiled = (await db.execute(select(Neuron.id).where(
            Neuron.id == -1, *sh.dormancy_exclusion_filters()))).first()
        check("compiled delivery excludes the dormant row", compiled is None)

        # ── runaway brake ───────────────────────────────────────────────
        over = sh.MAX_DORMANT_PER_RUN + 5
        for i in range(over):
            _plant(db, -100 - i, weight=below)
        await db.commit()
        braked = await sh.run_downscaling(db, cycles=1, since=SINCE, apply=True)
        awake = (await db.execute(select(func.count(Neuron.id)).where(
            Neuron.id <= -100, Neuron.dormant_at.is_(None)))).scalar()
        check("runaway brake refuses the whole batch",
              braked["refused"] is not None and not braked["dormant"],
              f"would_have_demoted="
              f"{(braked['refused'] or {}).get('would_have_demoted')} "
              f"> limit={sh.MAX_DORMANT_PER_RUN}")
        check("brake demotes NONE of the batch (no id-ordered truncation)",
              awake == over, f"{awake}/{over} still awake")
        results["brake"] = braked["refused"]


async def section_c() -> None:
    print("\nC. LIVE-CORPUS VERIFICATION")
    async with async_session() as db:
        planted = (await db.execute(select(func.count(Neuron.id)).where(
            Neuron.source_origin == PLANT_ORIGIN))).scalar()
        negative = (await db.execute(select(func.count(Neuron.id)).where(
            Neuron.id < 0))).scalar()
        changed = (await db.execute(select(func.count(Neuron.id)).where(
            Neuron.homeostatic_weight != 1.0))).scalar()
        dormant = (await db.execute(select(func.count(Neuron.dormant_at)))).scalar()
    check("no planted rows in live memory", planted == 0 and negative == 0)
    check("no live weight changed", changed == 0)
    check("no live row dormant", dormant == 0)


async def main() -> int:
    assert PROBE_DB.startswith(DISPOSABLE_PREFIXES), (
        f"PROBE_DB={PROBE_DB!r} is not a disposable database name; section B "
        f"TRUNCATES the neurons table it runs against")
    assert PROBE_DB not in ("corvus_mind", "corvus_locomo"), "refusing live db"

    await section_a()
    engine = create_async_engine(
        f"postgresql+asyncpg://yggdrasil@localhost/{PROBE_DB}")
    try:
        await section_b(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
    await section_c()

    failed = [c["name"] for c in results["checks"] if not c["pass"]]
    results["verdict"] = "GREEN" if not failed else "RED"
    results["failed"] = failed
    out = os.environ.get("PROBE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, default=str)
        print(f"\nreceipt: {out}")
    print(f"\nVERDICT: {results['verdict']}"
          + (f" — failed: {failed}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
