#!/usr/bin/env python3
"""
Full ATANT Evaluation: 250 narratives × 7 continuity properties.

Runs parallel ingestion + scoring, generates final report.
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

import os

import httpx

ATANT_TENANT = "corvus-atant"
# NEVER default to 8005 — that is the live corvus-mind service (see the
# 2026-07-13 contamination incident). main() verifies /tenant before ingest.
API_BASE = f"http://localhost:{int(os.environ.get('ATANT_PORT', '8007'))}"
NARRATIVES_FILE = Path(__file__).parent / "narratives" / "narratives_250.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

CONCURRENCY = 2  # Conservative: 6.5GB RAM, avoid OOM
MAX_RETRIES = 3
RETRY_DELAY = 2


async def api_call(method: str, path: str, payload: dict = None, retries: int = MAX_RETRIES) -> dict:
    """Make API call with retry logic."""
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{API_BASE}{path}"
                if method == "POST":
                    resp = await client.post(url, json=payload)
                elif method == "GET":
                    resp = await client.get(url)
                else:
                    raise ValueError(f"Unknown method {method}")

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[rate_limit] waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                else:
                    return {"error": resp.status_code, "detail": resp.text[:100]}
        except Exception as e:
            if attempt < retries - 1:
                await asyncio.sleep(RETRY_DELAY)
            else:
                return {"error": "connection_failed", "detail": str(e)[:100]}

    return {"error": "max_retries_exceeded"}


async def ingest_narrative_sessions(narrative: Dict) -> Dict:
    """Ingest all sessions of a narrative."""
    narrative_id = narrative["id"]
    category = narrative["category"]

    try:
        session_results = []
        for session in narrative.get("sessions", []):
            payload = {
                "lesson": session["text"],
                "evidence": f"ATANT {category} narrative {narrative_id} session {session['num']}",
                "label": f"atant_{narrative_id}_s{session['num']}",
                "summary": session["text"][:100],
                "authority_level": "informational"
            }

            result = await api_call("POST", "/remember", payload)
            session_results.append({
                "session_num": session["num"],
                "status": "ok" if "error" not in result else "failed",
                "result": result
            })

        return {
            "narrative_id": narrative_id,
            "category": category,
            "status": "ingested",
            "sessions": len(narrative.get("sessions", [])),
            "session_results": session_results
        }
    except Exception as e:
        return {
            "narrative_id": narrative_id,
            "category": category,
            "status": "error",
            "error": str(e)[:100]
        }


async def score_narrative(narrative: Dict, ingest_result: Dict) -> Dict:
    """Score continuity properties for a narrative."""
    narrative_id = narrative["id"]
    category = narrative["category"]
    scores = {}

    try:
        # Run checkpoint queries
        for session in narrative.get("sessions", []):
            for query_obj in session.get("queries", []):
                q = query_obj["q"]
                prop = query_obj["property"]

                # Run query
                payload = {"query": q}
                query_result = await api_call("POST", "/recall", payload)

                # Score: 1.0 if hits returned, 0.0 if error
                if "error" not in query_result and query_result.get("hits"):
                    score = 1.0
                else:
                    score = 0.0

                # Track property score
                if prop not in scores:
                    scores[prop] = []
                scores[prop].append(score)

        # Aggregate scores per property
        property_scores = {}
        for prop, score_list in scores.items():
            if score_list:
                property_scores[prop] = sum(score_list) / len(score_list)
            else:
                property_scores[prop] = 0.0

        return {
            "narrative_id": narrative_id,
            "category": category,
            "status": "scored",
            "property_scores": property_scores,
            "avg_score": sum(property_scores.values()) / len(property_scores) if property_scores else 0.0
        }

    except Exception as e:
        return {
            "narrative_id": narrative_id,
            "category": category,
            "status": "error",
            "error": str(e)[:100],
            "property_scores": {}
        }


async def run_full_eval(narratives: List[Dict]):
    """Run full evaluation with concurrency control."""
    print(f"\n{'='*70}")
    print(f"ATANT Full Evaluation: {len(narratives)} narratives")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Start: {datetime.now().isoformat()}")
    print(f"{'='*70}\n")

    # Phase 1: Ingestion
    print("Phase 1: Ingesting narratives...")
    ingest_results = []
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def ingest_with_semaphore(n):
        async with semaphore:
            result = await ingest_narrative_sessions(n)
            ingest_results.append(result)
            idx = len(ingest_results)
            if idx % 10 == 0:
                print(f"  [{idx}/{len(narratives)}] ingested")
            return result

    tasks = [ingest_with_semaphore(n) for n in narratives]
    await asyncio.gather(*tasks)

    print(f"✓ Ingestion complete: {len(ingest_results)} narratives")

    # Phase 2: Scoring
    print("\nPhase 2: Scoring continuity properties...")
    score_results = []

    async def score_with_semaphore(narrative, ingest_result):
        async with semaphore:
            result = await score_narrative(narrative, ingest_result)
            score_results.append(result)
            idx = len(score_results)
            if idx % 10 == 0:
                print(f"  [{idx}/{len(narratives)}] scored")
            return result

    tasks = [score_with_semaphore(n, i) for n, i in zip(narratives, ingest_results)]
    await asyncio.gather(*tasks)

    print(f"✓ Scoring complete: {len(score_results)} narratives")

    # Phase 3: Aggregate results
    print("\nPhase 3: Aggregating results...")

    by_property = {}
    by_category = {}
    overall_scores = []

    for result in score_results:
        cat = result["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(result)

        for prop, score in result.get("property_scores", {}).items():
            if prop not in by_property:
                by_property[prop] = []
            by_property[prop].append(score)

        if "avg_score" in result:
            overall_scores.append(result["avg_score"])

    # Compute aggregate stats
    agg_stats = {
        "total_narratives": len(narratives),
        "ingestion_success": sum(1 for r in ingest_results if r["status"] == "ingested"),
        "scoring_success": sum(1 for r in score_results if r["status"] == "scored"),
        "overall_accuracy": sum(overall_scores) / len(overall_scores) if overall_scores else 0.0,
        "by_property": {},
        "by_category": {}
    }

    for prop, scores in by_property.items():
        agg_stats["by_property"][prop] = {
            "mean": sum(scores) / len(scores) if scores else 0.0,
            "count": len(scores),
            "min": min(scores) if scores else 0.0,
            "max": max(scores) if scores else 0.0
        }

    for cat, results in by_category.items():
        cat_scores = [r.get("avg_score", 0.0) for r in results]
        agg_stats["by_category"][cat] = {
            "mean": sum(cat_scores) / len(cat_scores) if cat_scores else 0.0,
            "count": len(results)
        }

    # Print summary
    print("\n" + "="*70)
    print("ATANT EVALUATION SUMMARY")
    print("="*70)

    print(f"\nOverall Score: {agg_stats['overall_accuracy']:.2%}")
    print(f"Narratives: {agg_stats['total_narratives']} total, {agg_stats['ingestion_success']} ingested, {agg_stats['scoring_success']} scored")

    print("\nBy Property (7 Continuity Dimensions):")
    for prop in sorted(by_property.keys()):
        stats = agg_stats["by_property"][prop]
        print(f"  {prop:20s}: {stats['mean']:.2%} (n={stats['count']})")

    print("\nBy Category:")
    for cat in sorted(by_category.keys()):
        stats = agg_stats["by_category"][cat]
        print(f"  {cat:20s}: {stats['mean']:.2%} (n={stats['count']})")

    print(f"\nEnd: {datetime.now().isoformat()}")
    print("="*70 + "\n")

    # Save results
    results_file = RESULTS_DIR / "full_eval_results.json"
    with open(results_file, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "phase": "full_eval",
            "aggregates": agg_stats,
            "narratives": score_results[:10]  # Save first 10 for review
        }, f, indent=2)

    print(f"Results saved to {results_file}")

    return agg_stats


async def main():
    """Load narratives and run full eval."""
    tenant_id = ((await api_call("GET", "/tenant")) or {}).get("tenant_id")
    if tenant_id != ATANT_TENANT:
        print(f"FATAL: {API_BASE} is tenant '{tenant_id}', not '{ATANT_TENANT}'"
              " — refusing to ingest synthetic narratives into a real tenant.",
              file=sys.stderr)
        sys.exit(1)

    if not NARRATIVES_FILE.exists():
        print(f"Error: {NARRATIVES_FILE} not found")
        print("Run: python generate_narratives.py")
        sys.exit(1)

    with open(NARRATIVES_FILE) as f:
        data = json.load(f)
        narratives = data["narratives"]

    print(f"Loaded {len(narratives)} narratives from {NARRATIVES_FILE}")

    start_time = time.time()
    stats = await run_full_eval(narratives)
    elapsed = time.time() - start_time

    print(f"Evaluation complete in {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print(f"Average per narrative: {elapsed/len(narratives):.2f}s")

    return stats


if __name__ == "__main__":
    asyncio.run(main())
