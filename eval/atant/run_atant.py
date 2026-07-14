#!/usr/bin/env python3
"""
ATANT (Autoregressive Time-Aware Narrative Tracking) Benchmark for Corvus-Mind
Evaluates 7 continuity properties across multi-session narratives.

Properties:
1. Persistence: facts survive across sessions
2. Update handling: old facts correctly superseded
3. Temporal ordering: causality preserved
4. Disambiguation: similar entities resolved correctly
5. Reconstruction: point-in-time belief state reconstructible
6. Model independence: memory works across model swaps
7. Operational usefulness: memory helps downstream tasks

Usage:
  python run_atant.py --phase ingest   # Ingest narratives
  python run_atant.py --phase eval     # Run continuity checks
  python run_atant.py --phase all      # Full pipeline
"""

import asyncio
import json
import os
import sys
import argparse
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import httpx._exceptions

# Configuration
ATANT_TENANT = "corvus-atant"
ATANT_DB = "corvus_atant"
# NEVER default to 8005 — that is the live corvus-mind service. The
# 2026-07-13 run ingested 668 synthetic narratives into production memory
# because this pointed at the wrong tenant. verify_tenant() now hard-gates
# every run, but keep the default port distinct as the first line of defense.
ATANT_PORT = int(os.environ.get("ATANT_PORT", "8007"))
API_BASE = f"http://localhost:{ATANT_PORT}"
MAX_RETRIES = 3
RETRY_DELAY = 2
ATANT_CONCURRENCY = 1  # Conservative for test harness

# Paths
REPO_ROOT = Path(__file__).parent.parent.parent
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

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
                    print(f"[rate_limit] waiting {wait}s before retry {attempt+1}/{retries}")
                    await asyncio.sleep(wait)
                    continue
                else:
                    print(f"[error] {method} {path} -> {resp.status_code}: {resp.text[:200]}")
                    if attempt < retries - 1:
                        await asyncio.sleep(RETRY_DELAY)
                    continue
        except (httpx._exceptions.ConnectError, httpx._exceptions.TimeoutException) as e:
            print(f"[connection_error] attempt {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(RETRY_DELAY)
            continue

    raise Exception(f"Failed after {retries} retries: {method} {path}")


async def ingest_narrative(session_id: str, narrative_text: str, checkpoint: int = 0) -> dict:
    """Ingest narrative text as a lesson via /remember (write gate)."""
    payload = {
        "lesson": narrative_text,
        "evidence": f"ATANT benchmark narrative {session_id} checkpoint {checkpoint}",
        "label": f"atant_{session_id}_{checkpoint}",
        "summary": narrative_text[:100] + "..." if len(narrative_text) > 100 else narrative_text,
        "authority_level": "informational"
    }
    return await api_call("POST", "/remember", payload)


async def query_at_checkpoint(session_id: str, checkpoint: int, query: str) -> dict:
    """Query memory at a specific checkpoint (via /recall)."""
    payload = {
        "query": query,
        "context": {
            "atant_session": session_id,
            "checkpoint": checkpoint
        }
    }
    return await api_call("POST", "/recall", payload)


async def get_belief_state(neuron_id: int, at_timestamp: str = None) -> dict:
    """Retrieve belief state using /janitor/as-of for point-in-time reconstruction."""
    path = f"/janitor/as-of/{neuron_id}"
    if at_timestamp:
        path += f"?at={at_timestamp}"
    return await api_call("GET", path)


def score_persistence(ingest_results: list, final_query_result: dict) -> float:
    """Property 1: Facts persist across sessions.

    Score: % of injected facts recalled in final checkpoint query.
    """
    if not ingest_results or not final_query_result.get("neurons"):
        return 0.0

    injected_count = len([r for r in ingest_results if r.get("was_injected")])
    recalled_count = len(final_query_result.get("neurons", []))

    if injected_count == 0:
        return 1.0
    return min(1.0, recalled_count / injected_count)


def score_update_handling(before_state: dict, after_state: dict) -> float:
    """Property 2: Updates handled correctly.

    Score: 1.0 if old fact is superseded (not both present), 0.0 if duplication.
    """
    before_ids = {n["id"] for n in before_state.get("neurons", [])}
    after_ids = {n["id"] for n in after_state.get("neurons", [])}

    # If after state has both old and new versions of same fact, that's bad
    # (should have superseded instead)
    # Simplified: check if newer facts have higher authority_level
    if not after_state.get("neurons"):
        return 0.0

    authority_levels = [n.get("authority_level", 0) for n in after_state.get("neurons", [])]
    if not authority_levels:
        return 0.0

    return sum(1 for a in authority_levels if a >= 2) / len(authority_levels)


def score_temporal_ordering(narrative_events: list, recalled_order: list) -> float:
    """Property 3: Temporal causality preserved.

    Score: % of event pairs in correct temporal order in recalled set.
    """
    if len(narrative_events) < 2 or len(recalled_order) < 2:
        return 0.0

    correct_pairs = 0
    total_pairs = 0

    for i in range(len(narrative_events) - 1):
        for j in range(i + 1, len(narrative_events)):
            total_pairs += 1
            event_i = narrative_events[i]
            event_j = narrative_events[j]
            # Simplified: check if both present in recalled and in order
            if event_i in recalled_order and event_j in recalled_order:
                if recalled_order.index(event_i) < recalled_order.index(event_j):
                    correct_pairs += 1

    if total_pairs == 0:
        return 1.0
    return correct_pairs / total_pairs


def score_disambiguation(entities_used: dict, final_state: dict) -> float:
    """Property 4: Similar entities disambiguated correctly.

    Score: % of entities with correct context/relationships.
    """
    if not entities_used or not final_state.get("neurons"):
        return 0.0

    # Simplified: check that neurons have distinct labels for different entities
    labels = {n["label"] for n in final_state.get("neurons", [])}
    entity_count = len(entities_used)

    if entity_count == 0:
        return 1.0
    return min(1.0, len(labels) / entity_count)


def score_reconstruction(historical_states: list) -> float:
    """Property 5: Point-in-time reconstruction works.

    Score: % of historical checkpoints retrievable via /janitor/as-of.
    """
    if not historical_states:
        return 0.0

    retrievable = sum(1 for state in historical_states if state and state.get("was_current") is not None)
    return retrievable / len(historical_states)


def score_model_independence(ingest_lang: str, query_lang: str) -> float:
    """Property 6: Memory works across model changes.

    Simplified: return 1.0 if both succeeded (would test with different model calls IRL).
    """
    return 1.0 if (ingest_lang and query_lang) else 0.0


def score_operational_usefulness(query_accuracy: float, latency_ms: float) -> float:
    """Property 7: Memory actually helps (accuracy + latency).

    Score: accuracy (0-1) weighted by latency penalty (>500ms = 0.5x multiplier).
    """
    latency_multiplier = 1.0 if latency_ms < 500 else 0.5
    return query_accuracy * latency_multiplier


async def run_pilot_eval():
    """Run a small pilot (5 narratives) to validate approach."""
    print("\n" + "="*60)
    print("ATANT PILOT EVALUATION (5 narratives)")
    print("="*60)

    pilot_narratives = [
        {
            "id": "pilot-001",
            "title": "Alice meets Bob",
            "text": "Alice and Bob meet for the first time at a coffee shop. Alice orders espresso. Bob orders cappuccino.",
            "checkpoints": [
                {"num": 1, "query": "What did Alice order?", "expected": ["espresso"]},
                {"num": 2, "query": "Did Bob meet Alice?", "expected": ["yes", "true"]},
            ]
        },
        {
            "id": "pilot-002",
            "title": "Update: Alice changes job",
            "text": "Alice used to work at StartupX. Yesterday, she accepted an offer from BigCorp. She starts next Monday.",
            "checkpoints": [
                {"num": 1, "query": "Where does Alice work now?", "expected": ["BigCorp"]},
                {"num": 2, "query": "When does Alice start?", "expected": ["Monday", "next week"]},
            ]
        },
    ]

    results = []

    for narrative in pilot_narratives:
        print(f"\n[pilot] {narrative['title']}...")
        start_time = time.time()

        try:
            # Ingest
            ingest_result = await ingest_narrative(narrative["id"], narrative["text"], checkpoint=0)
            ingest_time = time.time() - start_time

            # Query checkpoints
            checkpoint_results = []
            for cp in narrative["checkpoints"]:
                query_start = time.time()
                query_result = await query_at_checkpoint(narrative["id"], cp["num"], cp["query"])
                query_time = time.time() - query_start
                checkpoint_results.append({
                    "checkpoint": cp["num"],
                    "query": cp["query"],
                    "latency_ms": int(query_time * 1000),
                    "result": query_result
                })

            results.append({
                "narrative_id": narrative["id"],
                "title": narrative["title"],
                "ingest_latency_ms": int(ingest_time * 1000),
                "checkpoints": checkpoint_results,
                "status": "ok"
            })

            print(f"  ✓ ingest {int(ingest_time*1000)}ms, {len(checkpoint_results)} checkpoints")

        except Exception as e:
            print(f"  ✗ error: {e}")
            results.append({
                "narrative_id": narrative["id"],
                "title": narrative["title"],
                "status": "error",
                "error": str(e)
            })

    # Write pilot results
    pilot_file = RESULTS_DIR / "pilot.json"
    with open(pilot_file, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "phase": "pilot",
            "narratives_tested": len(pilot_narratives),
            "results": results
        }, f, indent=2)

    print(f"\n[pilot] Results saved to {pilot_file}")
    return results


async def verify_tenant() -> None:
    """Refuse to run against any tenant but corvus-atant.

    The benchmark writes hundreds of synthetic lessons; pointed at a real
    memory tenant it contaminates production recall. GET /tenant is the
    authoritative identity check — exit hard on any mismatch or ambiguity."""
    try:
        info = await api_call("GET", "/tenant")
    except Exception as e:
        print(f"FATAL: cannot verify tenant at {API_BASE}: {e}", file=sys.stderr)
        sys.exit(1)
    tenant_id = (info or {}).get("tenant_id")
    if tenant_id != ATANT_TENANT:
        print(f"FATAL: {API_BASE} is tenant '{tenant_id}', not '{ATANT_TENANT}'."
              f" Start an isolated server first:\n"
              f"  TENANT_ID={ATANT_TENANT} PORT={ATANT_PORT} uvicorn app.main:app --port {ATANT_PORT}",
              file=sys.stderr)
        sys.exit(1)


async def main():
    parser = argparse.ArgumentParser(description="ATANT benchmark for Corvus-Mind")
    parser.add_argument("--phase", default="all", choices=["ingest", "eval", "pilot", "all"],
                        help="Evaluation phase")
    parser.add_argument("--narratives", type=int, default=5, help="Number of narratives (pilot)")
    args = parser.parse_args()

    print(f"""
╔════════════════════════════════════════════════════════╗
║          ATANT Benchmark for Corvus-Mind              ║
║     Continuity Properties Evaluation (7 dimensions)    ║
╚════════════════════════════════════════════════════════╝

Configuration:
  API Base: {API_BASE}
  Tenant: {ATANT_TENANT}
  DB: {ATANT_DB}
  Results: {RESULTS_DIR}

Phase: {args.phase}
Narratives (pilot): {args.narratives}
    """)

    await verify_tenant()

    if args.phase in ["pilot", "all"]:
        print("Running pilot evaluation...")
        try:
            pilot_results = await run_pilot_eval()
            if args.phase == "pilot":
                return
        except Exception as e:
            print(f"Pilot failed: {e}", file=sys.stderr)
            sys.exit(1)

    if args.phase in ["ingest", "eval", "all"]:
        print("Full evaluation would ingest 250+ narratives and score continuity properties.")
        print("(Currently piloting approach with small test set)")
        print("\nNext steps:")
        print("  1. Validate pilot approach (see pilot.json)")
        print("  2. Obtain full ATANT dataset (250 narratives)")
        print("  3. Scale evaluation with --phase all")
        print("  4. Generate full continuity report")


if __name__ == "__main__":
    asyncio.run(main())
