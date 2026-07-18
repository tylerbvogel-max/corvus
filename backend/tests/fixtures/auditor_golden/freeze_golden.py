"""Freeze the reconsolidation-auditor golden eval set (run ONCE, review diff).

Reconstructs the BEFORE state of every neuron in the 2026-07-15 manual
curation sweep from neuron_refinements old_values (first refinement of
that day per field wins — that is the state the manual reviewer saw),
pairs it with the accepted human disposition, and adds a deterministic
negative-control sample of healthy neurons the sweep did NOT touch.

Read-only against live corvus_mind. Output: golden.json beside this
script. Entries are split calibration/test by neuron-id parity so
thresholds are never tuned on the full set.

Run: cd backend && TENANT_ID=corvus-mind PYTHONPATH=. \
     venv/bin/python tests/fixtures/auditor_golden/freeze_golden.py
"""

import asyncio
import json
import os
from datetime import date

CURATION = os.path.expanduser(
    "~/.corvus-mind/curation/short-neurons-2026-07-15.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.json")
SWEEP_DAY = date(2026, 7, 15)
N_CONTROLS = 12

# Manual disposition vocabulary -> auditor disposition families. The eval
# scores family agreement, never word-for-word rewrite match.
DISPOSITION_MAP = {
    "enrich": "enrich", "correct-and-enrich": "enrich",
    "enrich-and-supersede-snapshot": "enrich",
    "adjacent-label-correction": "enrich",
    "keep": "keep", "merge": "merge",
    "deactivate": "deactivate", "deactivate-synthetic": "deactivate",
    "deactivate-stale-artifact": "deactivate",
    "deactivate-stale-transient-port": "deactivate",
    "deactivate-stale-harness-guidance": "deactivate",
    "deactivate-duplicate": "deactivate",
    "deactivate-stale-capacity": "deactivate",
}


async def main() -> None:
    from sqlalchemy import text
    from app.database import async_session

    with open(CURATION, encoding="utf-8") as fh:
        curation = json.load(fh)
    results = curation["results"]

    async with async_session() as db:
        entries = []
        for nid_str, res in sorted(results.items(), key=lambda kv: int(kv[0])):
            nid = int(nid_str)
            row = (await db.execute(text(
                "SELECT id, label, summary, content, department, node_type, "
                "authority_level, source_origin, citation, is_active, "
                "superseded_by, invocations, avg_utility, centrality, "
                "created_at, last_verified FROM neurons WHERE id = :nid"
            ), {"nid": nid})).mappings().first()
            if row is None:
                print(f"  !! neuron {nid} missing from DB — skipped")
                continue
            before = dict(row)
            # Rewind: the FIRST sweep-day refinement's old_value per field
            # is the state the human reviewer judged.
            refs = (await db.execute(text(
                "SELECT field, old_value FROM neuron_refinements "
                "WHERE neuron_id = :nid AND created_at::date = :day "
                "AND field IS NOT NULL ORDER BY id ASC"
            ), {"nid": nid, "day": SWEEP_DAY})).all()
            seen: set[str] = set()
            for field, old in refs:
                if field in seen or field not in (
                        "content", "summary", "label", "is_active",
                        "department", "superseded_by"):
                    continue
                seen.add(field)
                if field == "is_active":
                    before[field] = str(old).lower() == "true"
                elif field == "superseded_by":
                    before[field] = int(old) if old not in (None, "", "None") \
                        else None
                else:
                    before[field] = old
            # A sweep-deactivated neuron was ACTIVE when reviewed even if
            # no refinement row captured the flip via this path.
            disposition = DISPOSITION_MAP.get(res.get("disposition"))
            if disposition is None:
                print(f"  !! unmapped disposition {res.get('disposition')!r} "
                      f"for {nid} — skipped")
                continue
            if disposition == "deactivate":
                before["is_active"] = True
                before["superseded_by"] = None
            entries.append({
                "neuron_id": nid,
                "expected_family": disposition,
                "manual_disposition": res.get("disposition"),
                "manual_reason": res.get("reason"),
                "canonical_id": res.get("canonical_id"),
                "rewound_fields": sorted(seen),
                "split": "calibration" if nid % 2 == 0 else "test",
                "before": {k: (str(v) if k in ("created_at", "last_verified")
                               and v is not None else v)
                           for k, v in before.items()},
            })

        curated_ids = {e["neuron_id"] for e in entries}
        control_rows = (await db.execute(text(
            "SELECT id, label, summary, content, department, node_type, "
            "authority_level, source_origin, citation, is_active, "
            "superseded_by, invocations, avg_utility, centrality, "
            "created_at, last_verified FROM neurons "
            "WHERE is_active AND node_type IN "
            "('lesson', 'tool-profile', 'context-scope') "
            "AND invocations > 0 AND avg_utility >= 0.5 "
            "AND id != ALL(:ids) ORDER BY invocations DESC, id ASC LIMIT :n"
        ), {"ids": list(curated_ids), "n": N_CONTROLS})).mappings().all()
        controls = [{
            "neuron_id": r["id"], "expected_family": "keep",
            "manual_disposition": None, "is_control": True,
            "split": "calibration" if r["id"] % 2 == 0 else "test",
            "before": {k: (str(v) if k in ("created_at", "last_verified")
                           and v is not None else v)
                       for k, v in dict(r).items()},
        } for r in control_rows]

    payload = {
        "frozen_from": "corvus_mind live @ freeze time",
        "curation_source": CURATION,
        "sweep_day": str(SWEEP_DAY),
        "note": "BEFORE states rewound from neuron_refinements old_values; "
                "healthy controls are untouched active lessons. Immutable "
                "after review — re-running overwrites.",
        "defects": entries,
        "controls": controls,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    fam: dict[str, int] = {}
    for e in entries:
        fam[e["expected_family"]] = fam.get(e["expected_family"], 0) + 1
    print(f"frozen {len(entries)} defect entries {fam} "
          f"+ {len(controls)} healthy controls -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
