#!/usr/bin/env python3
"""The single command that says whether a deployed Corvus is actually serving.

Owned by roadmap record durability-operational-envelope (06), which absorbed
the interim version this replaces. That version checked one thing — /health
returning status "ok" — and /health has since become a pure LIVENESS probe
that deliberately touches no dependency. Asking it about the deployment is now
asking the wrong endpoint: it answers 200 with the database on fire.

What it asserts, in the order an operator cares about:

  liveness    the process answers at all
  readiness   dependencies are satisfied — schema head and the capability
              surface the profile CLAIMS actually mounted (GET /ready)
  schema      the Alembic revision, read from OUTSIDE the application, because
              a container can report healthy while its migrations silently
              failed and asking the app about itself would not catch it
  recall      a representative query returns hits — the system's actual job,
              not merely its plumbing. Runs with persist=false so verifying a
              deployment does not write to the graph it is verifying.
  jobs        scheduled work is not failing or overdue (GET /metrics/mind/jobs)
  slo         the service-level objectives are being met — a deployment can be
              up and still not delivering the behaviour users depend on
  atlas       the architecture extraction still matches the source it claims
              to describe
  provenance  the running artifact is the build that was tested

Only liveness, readiness, recall and jobs run by default: they need nothing but
the URL. The rest arm themselves when given what they need (--database-url,
--repo, --container), so this is one command in every environment rather than
a different invocation per environment.

Exit codes: 0 verified, 1 a check failed, 2 could not run a check.
"""

from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse


class CheckError(RuntimeError):
    """A check could not run, as distinct from a check that ran and failed."""


def _get(url: str, timeout: float) -> tuple[int, dict]:
    """Return (status, payload). A 503 is DATA here, not an exception —
    /ready answering 503 is the endpoint working correctly."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CheckError(f"{url} unreachable: {exc}") from exc


def _post(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CheckError(f"{url} unreachable: {exc}") from exc


# ---- checks -----------------------------------------------------------------

class _FrontendAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.modules = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "module" and attrs.get("src"):
            self.modules += 1
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.assets.append(attrs["href"])


def check_frontend(url: str, timeout: float) -> tuple[bool, str]:
    """Check the deployed HTML and its local assets, not a separate Vite build."""
    try:
        with urllib.request.urlopen(f"{url}/", timeout=timeout) as response:
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                return False, "frontend root did not return HTML"
            parser = _FrontendAssets()
            parser.feed(response.read().decode())
        if not parser.modules:
            return False, "frontend HTML has no module entrypoint"
        checked = 0
        for asset in parser.assets:
            target = urljoin(f"{url}/", asset)
            if urlparse(target).netloc != urlparse(url).netloc:
                continue  # Fonts/CDNs are not artifacts shipped in this image.
            with urllib.request.urlopen(target, timeout=timeout) as response:
                if response.status != 200 or response.headers.get_content_type() == "text/html":
                    return False, f"frontend asset missing or SPA fallback: {asset}"
                if not response.read(1):
                    return False, f"empty frontend asset: {asset}"
            checked += 1
        if not checked:
            return False, "frontend contains no local build assets"
        return True, f"frontend root and {checked} local build assets served"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"frontend unavailable: {exc}"


def check_image_identity(container: str, expected_image_id: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}}", container],
        text=True, capture_output=True,
    )
    if proc.returncode:
        raise CheckError(f"could not inspect {container}: {proc.stderr.strip()}")
    actual = proc.stdout.strip()
    if actual != expected_image_id:
        return False, f"deployed image {actual!r} differs from tested image {expected_image_id!r}"
    return True, f"deployed image matches tested config digest {actual}"


def check_packaged_atlas(url: str, timeout: float) -> tuple[bool, str]:
    status, payload = _get(f"{url}/admin/architecture", timeout)
    if status != 200 or not payload.get("boxes") or not payload.get("processes"):
        return False, f"packaged architecture missing or incomplete (HTTP {status})"
    if not payload.get("freshness", {}).get("fresh"):
        return False, "packaged architecture was not current when generated"
    return True, "packaged architecture includes component and process evidence"

def check_liveness(url: str, timeout: float) -> tuple[bool, str]:
    status, payload = _get(f"{url}/health", timeout)
    if status != 200:
        return False, f"/health returned HTTP {status}"
    if payload.get("status") != "ok":
        return False, f"/health reported status {payload.get('status')!r}"
    return True, f"process answering (tenant {payload.get('tenant', '?')})"


def check_readiness(url: str, timeout: float) -> tuple[bool, str]:
    status, payload = _get(f"{url}/ready", timeout)
    checks = payload.get("checks", [])
    failed = [c for c in checks if c.get("status") != "pass"]
    if status == 503 or payload.get("status") != "ready":
        detail = "; ".join(
            f"{c.get('name')}: {c.get('detail')}" for c in failed
        ) or f"HTTP {status}"
        return False, f"not ready — {detail}"
    return True, f"ready ({len(checks)} dependency checks passed)"


def check_recall(url: str, timeout: float, query: str) -> tuple[bool, str]:
    """The system's actual job. Plumbing can be green while recall returns
    nothing, which is the failure a user would notice first."""
    started = time.monotonic()
    status, payload = _post(
        f"{url}/recall",
        # persist=false: verifying a deployment must not write to the graph it
        # is verifying, or the check becomes a slow corpus contaminant.
        {"query": query, "top_k": 5, "persist": False},
        timeout,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if status != 200:
        return False, f"/recall returned HTTP {status}"
    hits = payload.get("hits") or []
    if not hits:
        return False, (
            f"/recall returned zero hits for {query!r} in {elapsed_ms}ms — "
            "the graph is empty, unembedded, or retrieval is broken"
        )
    return True, f"{len(hits)} hits in {elapsed_ms}ms (server {payload.get('latency_ms')}ms)"


def check_jobs(url: str, timeout: float) -> tuple[bool, str]:
    status, payload = _get(f"{url}/metrics/mind/jobs", timeout)
    if status != 200:
        return False, f"/metrics/mind/jobs returned HTTP {status}"
    jobs = payload.get("jobs", [])
    if not jobs:
        return False, "no scheduled jobs are inventoried"
    broken = [j for j in jobs if j.get("status") in ("failing", "late")]
    if broken:
        detail = "; ".join(f"{j['name']} {j['status']}: {j.get('detail','')}" for j in broken)
        return False, detail
    # never-run is NOT a failure: immediately after a deploy no timer has
    # ticked yet, and failing on that would make the command permanently red on
    # exactly the deployments it exists to verify. Reported, never fatal.
    pending = [j["name"] for j in jobs if j.get("status") == "never-run"]
    note = f" ({len(pending)} awaiting first run: {', '.join(pending)})" if pending else ""
    return True, f"{len(jobs) - len(pending)}/{len(jobs)} jobs reporting healthy{note}"


def check_slo(url: str, timeout: float) -> tuple[bool, str]:
    """Objectives, not just liveness. A deployment can be up and still not
    meeting the behaviour its users depend on."""
    status, payload = _get(f"{url}/metrics/mind/slo", timeout)
    if status != 200:
        return False, f"/metrics/mind/slo returned HTTP {status}"
    objectives = payload.get("objectives", [])
    if not objectives:
        return False, "no objectives are defined"
    breached = [o for o in objectives if o.get("status") == "breached"]
    if breached:
        return False, "; ".join(
            f"{o['id']}: {o['detail']} — {o['first_response']}" for o in breached
        )
    unknown = [o["id"] for o in objectives if o.get("status") == "unknown"]
    note = f" ({len(unknown)} signal(s) unavailable: {', '.join(unknown)})" if unknown else ""
    return True, f"{len(objectives) - len(unknown)}/{len(objectives)} objectives met{note}"


def check_schema(database_url: str, expected: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["psql", database_url, "-At", "-c", "SELECT version_num FROM alembic_version"],
        text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise CheckError(f"could not read alembic_version: {proc.stderr.strip()}")
    actual = proc.stdout.strip()
    if not actual:
        return False, "alembic_version is empty; migrations never ran"
    if expected and actual != expected:
        return False, f"schema at {actual!r}, expected {expected!r}"
    return True, f"schema at {actual}"


def check_atlas(repo: Path) -> tuple[bool, str]:
    script = repo / "backend" / "scripts" / "check_architecture.py"
    if not script.exists():
        raise CheckError(f"no architecture checker at {script}")
    proc = subprocess.run(
        [sys.executable, str(script), "--strict"],
        text=True, capture_output=True, cwd=str(repo / "backend"),
    )
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr).strip().splitlines()
        return False, tail[-1] if tail else "architecture check failed"
    return True, "architecture atlas current and conformant"


def check_provenance(container: str, expected_revision: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["docker", "inspect", "--format",
         '{{index .Config.Labels "org.opencontainers.image.revision"}}', container],
        text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise CheckError(f"could not inspect {container}: {proc.stderr.strip()}")
    actual = proc.stdout.strip()
    if expected_revision and actual != expected_revision:
        return False, (
            f"deployed artifact reports revision {actual!r}, expected "
            f"{expected_revision!r} — this is not the build that was tested"
        )
    return True, f"artifact revision {actual}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8005")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--recall-query",
                        default="how do I run the corvus backend tests",
                        help="representative query; must return at least one hit")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--expect-revision", default="")
    parser.add_argument("--repo", default="",
                        help="repository root, to check architecture atlas freshness")
    parser.add_argument("--container", default="")
    parser.add_argument("--expect-source-revision", default="")
    parser.add_argument("--expect-image-id", default="",
                        help="Docker image config digest from release provenance.json (image_id)")
    parser.add_argument("--frontend", action="store_true",
                        help="require HTML and local build assets from this deployment")
    parser.add_argument("--packaged-atlas", action="store_true",
                        help="require the architecture artifacts served by this deployment")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable results for CI artifacts")
    args = parser.parse_args()
    if args.expect_image_id and not args.container:
        parser.error("--expect-image-id requires --container")

    base = args.url.rstrip("/")
    planned: list[tuple[str, callable]] = [
        ("liveness", lambda: check_liveness(base, args.timeout)),
        ("readiness", lambda: check_readiness(base, args.timeout)),
        ("recall", lambda: check_recall(base, args.timeout, args.recall_query)),
        ("jobs", lambda: check_jobs(base, args.timeout)),
        ("slo", lambda: check_slo(base, args.timeout)),
    ]
    if args.database_url:
        planned.append(("schema",
                        lambda: check_schema(args.database_url, args.expect_revision)))
    if args.repo:
        planned.append(("atlas", lambda: check_atlas(Path(args.repo))))
    if args.container:
        planned.append(("provenance",
                        lambda: check_provenance(args.container, args.expect_source_revision)))
    if args.expect_image_id:
        planned.append(("image-identity",
                        lambda: check_image_identity(args.container, args.expect_image_id)))
    if args.frontend:
        planned.append(("frontend", lambda: check_frontend(base, args.timeout)))
    if args.packaged_atlas:
        planned.append(("packaged-atlas", lambda: check_packaged_atlas(base, args.timeout)))

    results: list[dict] = []
    failed = errored = 0
    # Every check runs even after one fails: an operator debugging a bad deploy
    # needs the whole picture, not the first symptom.
    for name, run in planned:
        try:
            ok, detail = run()
            mark = "PASS" if ok else "FAIL"
            failed += 0 if ok else 1
        except CheckError as exc:
            ok, detail, mark = False, str(exc), "ERROR"
            errored += 1
        results.append({"check": name, "ok": ok, "detail": detail, "result": mark})
        if not args.json:
            print(f"  [{mark}] {name}: {detail}")

    if args.json:
        print(json.dumps({
            "url": base, "checks": results,
            "verified": failed == 0 and errored == 0,
        }, indent=2))

    if errored:
        print(f"{errored} check(s) could not run", file=sys.stderr)
        return 2
    if failed:
        print(f"{failed} check(s) failed", file=sys.stderr)
        return 1
    if not args.json:
        print(f"deployment verified ({len(results)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
