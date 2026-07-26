"""Step 04 — extract the historical staleness supersessions with labels.

Sources, all append-only: every janitor pass's report archived under
lifecycle.events in the certificate summary JSONs, plus the artifact-dir
janitor-actions.jsonl / janitor-report.json files. Labels are harvested
from every id+label-bearing structure (fuse rows, judged/fast_path pairs,
reference flags) because the per-run eval databases no longer exist.

Note the conv-6 subtlety this extraction surfaced: the certificate conv-6
arm (20260720T212845Z) archived zero janitor events; the 6 conv-6
supersessions in the record's "55" belong to the earlier aborted run
(20260720T152320Z). Certificate-proper: 49 actions / 37 unique pairs.

Writes step04_unique_pairs.json (42 unique pairs incl. the aborted-run
conv-6 rows) into the step04 forensics artifact dir. Read-only otherwise.
"""
import json
import os

RES = os.path.expanduser("~/Projects/corvus/eval/locomo/results")
EVALS = os.path.expanduser("~/.corvus-mind/evals/locomo")
ART = os.path.join(EVALS, "20260726T155942Z-step04-supersession-forensics")
DIRS = {
    0: "20260719T125853Z-p24461-conv0-full-lifecycle",
    1: "20260719T143011Z-p6174-conv1-full-lifecycle",
    2: "20260719T183215Z-p15507-conv2-full-lifecycle",
    3: "20260719T235657Z-p24338-conv3-full-lifecycle",
    4: "20260720T050225Z-p2124-conv4-full-lifecycle",
    5: "20260720T102245Z-p29746-conv5-full-lifecycle",
    6: "20260720T152320Z-p28160-conv6-full-lifecycle",  # aborted conv-6 run
    7: "20260720T214354Z-p9380-conv7-full-lifecycle",
    8: "20260721T030558Z-p19090-conv8-full-lifecycle",
    9: "20260721T075744Z-p1304-conv9-full-lifecycle",
}


def harvest(obj, put):
    """Collect every id -> label pairing any janitor structure carries."""
    if isinstance(obj, dict):
        if ("a" in obj and "b" in obj and isinstance(obj.get("labels"), list)
                and len(obj["labels"]) == 2):
            put(obj["a"], obj["labels"][0])
            put(obj["b"], obj["labels"][1])
        for k, v in obj.items():
            if k.endswith("_id") and isinstance(v, int):
                lab = obj.get(k[:-3] + "_label")
                if isinstance(lab, str):
                    put(v, lab)
        for v in obj.values():
            harvest(v, put)
    elif isinstance(obj, list):
        for v in obj:
            harvest(v, put)


def main():
    out = []
    for conv in range(10):
        idmap = {}

        def put(nid, label, m=idmap):
            if label:
                m.setdefault(int(nid), label)

        summary = json.load(open(
            os.path.join(RES, f"conv{conv}-summary-strict-full-lifecycle.json")))
        harvest(summary.get("lifecycle", {}), put)
        sup = []
        ja = os.path.join(EVALS, DIRS[conv], "episodes", "janitor-actions.jsonl")
        if os.path.exists(ja):
            for line in open(ja):
                r = json.loads(line)
                harvest(r, put)
                if r.get("action") == "staleness.superseded":
                    sup.append({"finding_id": r.get("finding_id"),
                                "newer": r["newer"], "older": r["older"],
                                "older_utility": r.get("older_utility")})
        jr = os.path.join(EVALS, DIRS[conv], "episodes", "janitor-report.json")
        if os.path.exists(jr):
            harvest(json.load(open(jr)), put)
        for e in (summary.get("lifecycle") or {}).get("events") or []:
            for s in (e.get("report") or {}).get("staleness", {}).get("superseded", []):
                if not any(x.get("finding_id") == s.get("finding_id")
                           and x["newer"] == s["newer"] for x in sup):
                    sup.append({"finding_id": s.get("finding_id"),
                                "newer": s["newer"], "older": s["older"],
                                "older_utility": s.get("older_utility")})
        for s in sup:
            out.append({"conv": conv, **s,
                        "newer_label": idmap.get(s["newer"]),
                        "older_label": idmap.get(s["older"])})

    uniq = {}
    for r in out:
        uniq.setdefault((r["conv"], r["newer"], r["older"]), r)
    rows = list(uniq.values())
    both = sum(1 for r in rows if r["newer_label"] and r["older_label"])
    print(f"actions: {len(out)}  unique: {len(rows)}  both-labels: {both}")
    path = os.path.join(ART, "step04_unique_pairs.json")
    with open(path, "w") as f:
        json.dump(rows, f, indent=1)
    print("written:", path)


main()
