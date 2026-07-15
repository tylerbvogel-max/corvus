#!/usr/bin/env python3
"""Live cross-harness capability parity probe.

Fetches one capsule from the running brain, evaluates every declared body,
and exits non-zero on integrity, identity, governance, memory, or tool gaps.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.capability_capsule import load_harness_profiles, transfer_health, verify  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8005")
    parser.add_argument("--allow-degraded", action="store_true")
    args = parser.parse_args()
    with urllib.request.urlopen(args.backend + "/capabilities/capsule", timeout=10) as response:
        capsule = json.load(response)
    result = {"capsule_id": capsule.get("capsule_id"), "integrity": verify(capsule), "harnesses": {}}
    failed = not result["integrity"]["valid"]
    for harness_id in load_harness_profiles():
        health = transfer_health(capsule, harness_id)
        result["harnesses"][harness_id] = health
        failed = failed or (health["status"] != "full" and not args.allow_degraded)
    print(json.dumps(result, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
