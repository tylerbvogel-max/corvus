"""Shared loader for LoCoMo certificate forensics.

Read-only over eval/locomo/results/. Never writes into the evidence dirs.
"""
import json
import os
import re
from collections import defaultdict

RESULTS = os.path.expanduser("~/Projects/corvus/eval/locomo/results")
ARTIFACTS = os.path.expanduser("~/.corvus-mind/evals/locomo")
ARMS = ["memory", "nospread", "embed-only", "baseline"]
CONVS = list(range(10))
CATNAME = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
REFUSAL = "no information available"


def load(conv, arm, suffix="strict-full-lifecycle"):
    p = os.path.join(RESULTS, f"conv{conv}-{arm}-{suffix}.json")
    with open(p) as f:
        return json.load(f)


def load_summary(conv, suffix="strict-full-lifecycle"):
    with open(os.path.join(RESULTS, f"conv{conv}-summary-{suffix}.json")) as f:
        return json.load(f)


def question_key(r):
    return (r["question"].strip(), r["category"])


def build():
    """Return {(conv, qidx): {meta..., arm -> record}} with alignment verified."""
    table = {}
    misalign = []
    for c in CONVS:
        per_arm = {a: load(c, a)["results"] for a in ARMS}
        n = {a: len(v) for a, v in per_arm.items()}
        if len(set(n.values())) != 1:
            misalign.append((c, n))
        base = per_arm["memory"]
        for i, r in enumerate(base):
            row = {"conv": c, "qidx": i, "question": r["question"], "gold": r["gold"],
                   "category": r["category"], "cat": CATNAME[r["category"]], "arms": {}}
            for a in ARMS:
                rr = per_arm[a][i] if i < len(per_arm[a]) else None
                if rr is not None and rr["question"].strip() != r["question"].strip():
                    # fall back to text match
                    m = [x for x in per_arm[a] if x["question"].strip() == r["question"].strip()]
                    rr = m[0] if m else None
                    misalign.append((c, a, i))
                if rr is not None:
                    row["arms"][a] = rr
            table[(c, i)] = row
    return table, misalign


def is_refusal(pred):
    return REFUSAL in (pred or "").lower()


def norm_tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


STOP = set("the a an of in on at to for and or is was were be been by with as it its that this "
           "he she they them his her their from about into over under after before".split())


def content_tokens(s):
    return norm_tokens(s) - STOP


def pct(x, n):
    return 0.0 if not n else round(100.0 * x / n, 2)
