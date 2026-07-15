#!/usr/bin/env python3
"""Live Mission Control proof: contract, lock, self-audit rejection, invalidation."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8005"


def call(path: str, method: str = "GET", payload: dict | None = None, expected: int = 200):
    req = urllib.request.Request(BASE + path, method=method,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            assert response.status == expected
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == expected:
            return json.loads(exc.read())
        raise


def main() -> None:
    dashboard = call("/agency-lab/dashboard")
    policies = call("/agency-lab/policies")
    assert dashboard["workers"] and policies
    worker, policy = dashboard["workers"][0], policies[0]
    key = f"live-intake-{int(time.time())}"
    graph = {"mission": "Live-test truthful one-turn intake", "constraints": ["read-only"],
      "kill_criteria": ["unsupported claims"], "nodes": [
        {"id": "intake", "title": "Intake", "outcome": "evidence-linked brief",
         "acceptance": ["facts separated from inference", "limitations disclosed"], "risk_tier": 1, "task_class": "intake"},
        {"id": "synthesis", "title": "Synthesis", "outcome": "accepted recommendation",
         "acceptance": ["upstream intake verified"], "risk_tier": 2, "task_class": "synthesis"}],
      "edges": [{"from": "intake", "to": "synthesis"}]}
    venture = call("/agency-lab/ventures", "POST", {"key": key, "title": "Live intake proof",
        "graph": graph, "change_reason": "planted live verification", "created_by": "live-probe"})
    orders = []
    for node in ("intake", "synthesis"):
        orders.append(call("/agency-lab/work-orders", "POST", {"venture_key": key, "node_id": node,
            "worker_profile_id": worker["id"], "policy_id": policy["id"],
            "permissions": {"filesystem": "read_only", "commands": ["read", "search"]}, "ttl_minutes": 15}))
    upstream, downstream = orders
    origin = call(f"/agency-lab/work-orders/{upstream['id']}/origin")
    assert origin["reward_contract"]["digest"] == upstream["contract"]["digest"]
    completion = call(f"/agency-lab/work-orders/{upstream['id']}/complete", "POST", {
        "disposition": "partial", "claims": [{"claim": "intake inspected", "evidence_ids": ["probe"]}],
        "confidence": .62, "limitations": ["synthetic input only"],
        "disclosures": [{"type": "uncertainty", "detail": "no external corpus"}],
        "evidence": [{"id": "probe", "kind": "live_api_round_trip"}], "next_action": "independent audit"})
    duplicate = call(f"/agency-lab/work-orders/{upstream['id']}/complete", "POST", {
        "disposition": "complete", "claims": [], "confidence": 1, "limitations": [],
        "disclosures": [], "evidence": [], "next_action": "none"}, expected=409)
    self_audit = call(f"/agency-lab/work-orders/{upstream['id']}/audit", "POST", {
        "passed": True, "verifier": worker["key"], "evidence": {}, "defects": []}, expected=409)
    failed = call(f"/agency-lab/work-orders/{upstream['id']}/audit", "POST", {
        "passed": False, "verifier": "independent-live-critic", "evidence": {"test": "planted"},
        "defects": [{"claim": "intake inspected", "finding": "insufficient corpus"}]})
    assert downstream["id"] in failed["invalidated"]
    events = call(f"/agency-lab/work-orders/{upstream['id']}/events")
    print(json.dumps({"venture": venture, "upstream": upstream["id"], "downstream": downstream["id"],
        "contract_digest": upstream["contract"]["digest"], "completion": completion,
        "duplicate_rejected": duplicate["detail"], "self_audit_rejected": self_audit["detail"],
        "audit": failed, "events": [x["event_type"] for x in events]}, indent=2))


if __name__ == "__main__":
    main()
