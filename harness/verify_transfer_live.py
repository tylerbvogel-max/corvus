#!/usr/bin/env python3
"""Planted live round-trip for the Capability Capsule transport boundary."""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.capability_capsule import memory_content_hash, seal, stable_memory_id  # noqa: E402


def get(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8005")
    args = parser.parse_args()
    capsule = get(args.backend + "/capabilities/capsule")

    _, duplicate = post(args.backend + "/capabilities/reconcile", {
        "capsule": capsule, "target_harness": "codex", "apply": False})
    assert duplicate["memory"]["new"] == 0, duplicate["memory"]

    planted = {
        "source_neuron_id": "live-transfer-probe", "kind": "lesson",
        "label": "Capability Capsule live parity verified across harnesses",
        "content": "On 2026-07-14 the live Capability Capsule parity probe returned full coverage for Claude Code and Codex, and explicit degraded coverage for OpenCode because it lacks advisory pre-tool context.",
        "summary": "Live transfer parity: Claude Code 100%, Codex 100%, OpenCode 97.5% with its missing pre-tool lifecycle surfaced rather than hidden.",
        "region": "Projects", "portability_scope": "project", "project": "corvus",
        "authority_level": "informational", "utility": 0.5,
        "source_origin": "live_transfer_probe", "source_version": "1.0.0",
        "created_at": "2026-07-14T20:05:00-05:00", "last_verified": "2026-07-14T20:05:00-05:00",
        "superseded_by": None,
    }
    planted["memory_id"] = stable_memory_id(planted)
    planted["content_hash"] = memory_content_hash(planted)
    already_planted = planted["memory_id"] in {x["memory_id"] for x in capsule["memories"]}
    capsule.pop("integrity", None)
    if not already_planted:
        capsule["memories"].append(planted)
    seal(capsule)

    _, preview = post(args.backend + "/capabilities/reconcile", {
        "capsule": capsule, "target_harness": "codex", "apply": False})
    assert preview["memory"]["new"] == (0 if already_planted else 1), preview["memory"]

    tampered = json.loads(json.dumps(capsule))
    target = next(x for x in tampered["memories"] if x["memory_id"] == planted["memory_id"])
    target["content"] += " TAMPERED"
    try:
        post(args.backend + "/capabilities/verify", {
            "capsule": tampered, "target_harness": "codex"})
        raise AssertionError("tampered capsule was accepted")
    except urllib.error.HTTPError as exc:
        assert exc.code == 422, exc.code

    _, applied = post(args.backend + "/capabilities/reconcile", {
        "capsule": capsule, "target_harness": "codex", "apply": True})
    statuses = [x["status"] for x in applied["applied"]]
    assert statuses == ([] if already_planted else ["gated"]), statuses

    final = get(args.backend + "/capabilities/capsule")
    final_ids = {x["memory_id"] for x in final["memories"]}
    assert planted["memory_id"] in final_ids
    print(json.dumps({
        "duplicate_preview": duplicate["memory"],
        "new_preview": preview["memory"],
        "already_planted": already_planted,
        "tamper_status": 422,
        "apply_statuses": statuses,
        "reexport_contains": planted["memory_id"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
