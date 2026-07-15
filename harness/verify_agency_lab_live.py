#!/usr/bin/env python3
"""Live planted economy proof for Agency Lab."""

import argparse
import json
import urllib.request


def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8005")
    args = parser.parse_args()
    initial = call(args.backend, "/agency-lab/dashboard")
    policy_id = initial["active_policy_id"]
    run = initial["summary"]["workers"] + 1

    workers = {}
    for role in ("builder", "critic", "adjudicator"):
        workers[role] = call(args.backend, "/agency-lab/workers", {
            "key": f"live-{role}-{run}", "display_name": f"Live {role.title()} {run}",
            "role": role, "model": "opus", "harness": "codex",
            "task_classes": ["coding"], "risk_tiers": [1, 2, 3], "skill_ids": [],
            "permission_ceiling": "project_reversible", "starting_capital": 100,
        })
    experiment = call(args.backend, "/agency-lab/experiments", {
        "name": f"Live disclosure economy proof {run}", "mode": "simulation",
        "policy_ids": [policy_id],
        "dimensions": {"model": ["opus"], "harness": ["codex"],
                       "task_class": ["coding"], "risk_tier": [1, 2, 3]},
        "primary_metric": "verified_value_retained_7d",
        "guardrails": {"critic_precision_min": 0.6}, "sample_target": 12,
    })

    def score(worker, event_type, **extra):
        payload = {"worker_profile_id": workers[worker]["id"], "policy_id": policy_id,
                   "experiment_id": experiment["id"], "work_order_id": f"live-proof-{run}",
                   "event_type": event_type, "task_class": "coding", "risk_tier": 2,
                   "evidence": {"probe": "verify_agency_lab_live.py"}}
        payload.update(extra)
        return call(args.backend, "/agency-lab/score", payload)

    results = {
        "disclosure": score("builder", "self_disclosure", pre_submission=True, reproducible=True),
        "completion": score("builder", "verified_completion", forecast_probability=0.9,
                            resolved_outcome=True),
        "critic_finding": score("critic", "verified_critic_finding", reproducible=True),
        "critic_false_1": score("critic", "critic_false_positive"),
        "critic_false_2": score("critic", "critic_false_positive"),
        "concealment": score("builder", "concealment", known_failure=True),
        "tampering": score("adjudicator", "evaluator_tampering"),
    }
    results["disclosure_settlement"] = call(args.backend,
        f"/agency-lab/scores/{results['disclosure']['id']}/settle", {
            "passed": True, "evidence": {"probe": "7-day replay passed"}})
    results["completion_clawback"] = call(args.backend,
        f"/agency-lab/scores/{results['completion']['id']}/settle", {
            "passed": False, "evidence": {"probe": "delayed regression planted"}})
    final = call(args.backend, "/agency-lab/dashboard")
    indexed = {w["key"]: w for w in final["workers"]}
    builder = indexed[f"live-builder-{run}"]
    adjudicator = indexed[f"live-adjudicator-{run}"]
    codes = {d["code"] for d in final["diagnostics"]}
    assert results["disclosure"]["points_total"] == 11
    assert results["disclosure"]["settled_points"] == 6.6
    assert results["disclosure"]["escrow_points"] == 4.4
    assert results["completion"]["points_total"] == 13.92
    assert results["concealment"]["points_total"] == -60
    assert builder["agency_capital"] == 45.43
    assert results["disclosure_settlement"]["capital_adjustment"] == 4.4
    assert results["completion_clawback"]["capital_adjustment"] == -13.92
    assert adjudicator["status"] == "quarantined"
    assert {"critic_spam", "integrity_breach"} <= codes
    print(json.dumps({"policy_id": policy_id, "experiment_id": experiment["id"],
        "worker_ids": {k: v["id"] for k, v in workers.items()},
        "planted_results": results, "builder": builder, "adjudicator": adjudicator,
        "diagnostics": final["diagnostics"], "summary": final["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
