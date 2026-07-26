"""Step 03 (mind-calibrated-abstention): where the refusals' evidence lives.

For every non-adversarial refusal in the v3 memory arm (verifier
conversions + draft self-refusals), measure gold-answer content-token
coverage in (a) the DELIVERED top-10 memories (re-recalled live) and
(b) the whole active graph. Null control: other conversations' golds
against this graph.

Splits blame between ingest absence (not in graph), delivery failure
(in graph, not in top-10 -> Steps 05/06), and policy (delivered but
silenced). Read-only DB; aggregates only.

Run (backend venv, memory-tenant env, TENANT_ID=corvus-locomo — see
run_locomo.sh for the sourcing pattern). As-run version + artifact:
~/.corvus-mind/evals/locomo/*-step03-abstention-probes/.
"""
import asyncio
import json
import os
import random
import sys

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ANALYSIS_DIR)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "eval/locomo"))
sys.path.insert(0, ANALYSIS_DIR)

from run_locomo import ARM_CONFIG, load_dataset, recall_hits  # noqa: E402
from lib import content_tokens  # noqa: E402

RESULTS = os.path.join(REPO, "eval/locomo/results")
V3 = "conv0-memory-verified-full-lifecycleverifier-full-v3.json"
OUT = os.path.join(os.environ.get("STEP03_OUT_DIR", os.getcwd()),
                   "step03_refusal_presence.json")
SEM = asyncio.Semaphore(4)  # embedding-only recall, no CLI subprocesses


def cov(gold_tokens, text_tokens):
    return (len(gold_tokens & text_tokens) / len(gold_tokens)
            if gold_tokens else None)


async def main():
    from app.config import settings
    for flag, value in ARM_CONFIG["memory"].items():
        object.__setattr__(settings, flag, value)
    from app.services.actions.init_registry import init_actions_registry
    init_actions_registry()
    from sqlalchemy import text as sqltext
    from app.database import async_session

    async with async_session() as db:
        rows = (await db.execute(sqltext(
            "SELECT label, coalesce(content,'') FROM neurons "
            "WHERE node_type='lesson' AND is_active"))).all()
    graph_vocab = set()
    for label, content in rows:
        graph_vocab |= content_tokens(label + " " + content)
    print(f"active lesson neurons: {len(rows)}, graph vocab: {len(graph_vocab)}")

    with open(os.path.join(RESULTS, V3)) as fh:
        rs = json.load(fh)["results"]
    pool = [r for r in rs if r["category"] != 5
            and (r.get("verifier") or {}).get("verdict")
            in ("draft-refused", "unsupported")]

    async def one(r):
        async with SEM:
            async with async_session() as db:
                hits, _ = await recall_hits(db, r["question"])
        g = content_tokens(r["gold"])
        return {"verdict": r["verifier"]["verdict"],
                "category": r["category"],
                "top1_sim": (r.get("retrieval") or {}).get("top1_sim"),
                "cov_delivered": cov(g, content_tokens(" ".join(hits))),
                "cov_graph": cov(g, graph_vocab)}

    out = [o for o in await asyncio.gather(*[one(r) for r in pool])
           if o["cov_graph"] is not None]

    data = load_dataset()
    rng = random.Random(70326)
    other_golds = [str(q.get("answer", "")) for c in data[1:]
                   for q in c["qa"] if q.get("category") != 5]
    null_cov = [c for c in (cov(content_tokens(gld), graph_vocab)
                            for gld in rng.sample(other_golds, 200))
                if c is not None]
    print(f"null control (other convs' golds vs graph): "
          f"mean {sum(null_cov)/len(null_cov):.3f}, full-coverage rate "
          f"{sum(1 for c in null_cov if c >= 0.999)/len(null_cov):.3f}")

    for name, grp in (
            ("draft-refused", [o for o in out if o["verdict"] == "draft-refused"]),
            ("conversions", [o for o in out if o["verdict"] == "unsupported"]),
            ("ALL refusals", out)):
        n = len(grp)
        full_g = sum(1 for o in grp if o["cov_graph"] >= 0.999)
        full_d = sum(1 for o in grp if o["cov_delivered"] >= 0.999)
        stranded = sum(1 for o in grp
                       if o["cov_graph"] >= 0.999 and o["cov_delivered"] < 0.999)
        print(f"\n{name} (n={n}): mean cov graph "
              f"{sum(o['cov_graph'] for o in grp)/n:.3f} / delivered "
              f"{sum(o['cov_delivered'] for o in grp)/n:.3f}; FULL in graph "
              f"{full_g}, FULL in delivered {full_d}")
        print(f"  in-graph-but-not-delivered (Step 05/06 territory): {stranded}")
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
