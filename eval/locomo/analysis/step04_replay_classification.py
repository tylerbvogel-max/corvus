"""Step 04 replay — run the NEW conflict classifier (with resolution_hint)
over the label-recoverable historical supersession pairs and report how many
the durability gate would have blocked.

Fidelity caveat, stated up front: the certificate-era neuron CONTENT died
with the per-run eval databases; only labels were recoverable from the
append-only artifacts. The replay therefore classifies label text (which is
what the janitor-report also logs), not full content. Repeat-fire blocking
(13 of 55 actions) is structural and needs no LLM.

Read-only over evidence; writes one new receipts file into the step04
forensics artifact dir. Uses the real classify lane (llm_provider CLI path).

Usage: TENANT_ID=corvus-locomo python3 step04_replay_classification.py
(run from backend/ so `app` imports resolve, or with PYTHONPATH=backend)
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


async def main():
    from app.services.integrity.conflict_monitor import _classify_batch
    from app.services.integrity.similarity import SimilarPair

    rows = json.load(open(os.path.join(
        ART, "step04_historical_classification.json")))["rows"]
    labeled = [r for r in rows if r["newer_label"] and r["older_label"]]
    print(f"replaying {len(labeled)} of {len(rows)} unique pairs "
          f"(both labels recovered)")

    pairs, content = [], {}
    for i, r in enumerate(labeled):
        pairs.append(SimilarPair(
            neuron_a_id=i * 2, neuron_b_id=i * 2 + 1, similarity=0.8,
            a_label=r["older_label"], b_label=r["newer_label"]))
        content[i * 2] = (r["older_label"], r["older_label"])
        content[i * 2 + 1] = (r["newer_label"], r["newer_label"])

    verdicts = []
    for start in range(0, len(pairs), 5):
        batch = pairs[start:start + 5]
        out = await _classify_batch(batch, content)
        for cls in out:
            idx = cls.get("pair_index", -1)
            if 0 <= idx < len(batch):
                verdicts.append({
                    "pair": labeled[start + idx],
                    "classification": cls.get("classification"),
                    "resolution_hint": cls.get("resolution_hint"),
                    "reasoning": cls.get("reasoning", "")[:300],
                })
        print(f"  batch {start // 5 + 1}: {len(out)} verdicts")

    would_supersede = [v for v in verdicts
                      if v["classification"] == "contradictory"
                      and v["resolution_hint"] == "state_update"]
    blocked = [v for v in verdicts if v not in would_supersede]
    summary = {
        "replayed": len(verdicts),
        "would_still_supersede": len(would_supersede),
        "blocked_by_new_logic": len(blocked),
        "blocked_breakdown": {},
    }
    for v in blocked:
        k = f"{v['classification']}/{v['resolution_hint'] or '-'}"
        summary["blocked_breakdown"][k] = summary["blocked_breakdown"].get(k, 0) + 1
    path = os.path.join(ART, "step04_replay_receipts.json")
    with open(path, "w") as f:
        json.dump({"summary": summary, "verdicts": verdicts}, f, indent=1)
    print(json.dumps(summary, indent=1))
    print("written:", path)


asyncio.run(main())
