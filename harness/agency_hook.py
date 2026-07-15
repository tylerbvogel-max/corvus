#!/usr/bin/env python3
"""Harness-agnostic Corvus Agency hook: origin injection and exit submission.

Any harness that can call a command at session start/stop can use this adapter.
JSON is emitted to stdout; diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    base = os.getenv("CORVUS_URL", "http://127.0.0.1:8005")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("CORVUS_MEMORY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Corvus hook failed ({exc.code}): {exc.read().decode()}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    origin = sub.add_parser("origin"); origin.add_argument("work_order_id")
    stop = sub.add_parser("stop"); stop.add_argument("work_order_id"); stop.add_argument("completion_json")
    args = parser.parse_args()
    if args.stage == "origin":
        result = request(f"/agency-lab/work-orders/{args.work_order_id}/origin")
        result["agent_instruction"] = "Treat this contract as immutable. Disclose uncertainty early; submit the required exit record before stopping."
    else:
        with open(args.completion_json, encoding="utf-8") as handle:
            result = request(f"/agency-lab/work-orders/{args.work_order_id}/complete", "POST", json.load(handle))
    json.dump(result, sys.stdout, indent=2, default=str); sys.stdout.write("\n")


if __name__ == "__main__":
    main()
