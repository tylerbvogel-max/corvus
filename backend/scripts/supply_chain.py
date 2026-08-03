#!/usr/bin/env python3
"""Supply-chain checks over the source tree and the built release artifact.

Every check here is pip-installable, so the same command runs locally and in CI.
That is deliberate: syft / trivy / gitleaks / cosign are not installed on the
development machine, and a control that only ever executes on a CI runner is a
control nobody can reproduce when it fires.

Subcommands
    audit       known-vulnerability audit of the locked dependency set
    sbom        CycloneDX SBOM for the locked dependency set
    secrets     credential scan of the source tree
    artifact    inspect a built image for baked credentials and dead paths
    provenance  emit a provenance record describing what was built and from what

Artifacts land under .artifacts/supply-chain/, matching the convention already
used by run_test_lane.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
LOCK = BACKEND_ROOT / "requirements.txt"
ALLOWLIST = BACKEND_ROOT / "supply-chain-allowlist.json"
ARTIFACT_DIR = REPO_ROOT / ".artifacts/supply-chain"

# Base images are digest-pinned in the Dockerfile; provenance records which.
_FROM_DIGEST = re.compile(r"^FROM\s+(\S+@sha256:[0-9a-f]{64})", re.MULTILINE)


def _load_allowlist() -> tuple[set[str], list[str]]:
    """Return (accepted advisory ids, entries past their review date).

    A permanent exception list is just a suppressed alarm, so entries carry a
    review_by date and the gate fails once one lapses. Accepting an advisory is
    cheap; accepting it forever is not.
    """
    if not ALLOWLIST.exists():
        return set(), []
    import datetime as dt

    doc = json.loads(ALLOWLIST.read_text())
    today = dt.date.today()
    accepted: set[str] = set()
    stale: list[str] = []
    for entry in doc.get("accepted", []):
        vid = entry.get("id")
        if not vid:
            continue
        accepted.add(vid)
        review_by = entry.get("review_by")
        if review_by and dt.date.fromisoformat(review_by) < today:
            stale.append(f"{entry.get('package')} {vid} (review_by {review_by})")
    return accepted, stale


def _artifact_dir() -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_DIR


def _tool(name: str) -> str | None:
    """Locate a genuine external binary. PATH is the only place it can be."""
    return shutil.which(name)


def _python_tool(name: str, module: str) -> list[str] | None:
    """Resolve a pip-installed console script to an argv prefix, or None.

    PATH alone is the wrong question for these. Every documented invocation in
    this repo runs scripts with the venv interpreter directly —
    ``./venv/bin/python scripts/supply_chain.py audit`` — which does NOT put
    venv/bin on PATH. ``shutil.which`` then missed a pip-audit sitting right
    beside the interpreter and the gate reported "not installed" and exited 2:
    a real failure, but one that reads as a pass to anyone who pipes the output
    through ``tail`` and reads the last line instead of the status. A security
    gate that can be mistaken for green when it never ran is worse than one that
    is merely absent.

    Order is deliberate. The console script next to ``sys.executable`` wins
    because it belongs to the very interpreter running this file, so the audit
    describes the environment the caller meant rather than whichever one happens
    to be first on PATH. Then PATH, for a system-wide install. Then ``-m``,
    which still works when a wheel shipped the module without generating its
    console script.
    """
    beside = Path(sys.executable).parent / name
    if beside.exists():
        return [str(beside)]
    on_path = shutil.which(name)
    if on_path:
        return [on_path]
    if importlib.util.find_spec(module) is not None:
        return [sys.executable, "-m", module]
    return None


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


# ── audit ──

def cmd_audit(args: argparse.Namespace) -> int:
    tool = _python_tool("pip-audit", "pip_audit")
    if not tool:
        print(
            f"pip-audit is not installed in {sys.executable}: "
            f"{sys.executable} -m pip install pip-audit\n"
            "THE AUDIT DID NOT RUN — this is a failure, not a skip.",
            file=sys.stderr,
        )
        return 2
    out = _artifact_dir() / "pip-audit.json"
    proc = _run([
        *tool,
        "--requirement", str(LOCK),
        "--format", "json",
        "--output", str(out),
        # The lock already contains the full transitive closure, so there is
        # nothing for pip-audit to resolve. Without this it tries to install the
        # whole torch chain into a scratch env just to learn version numbers.
        "--no-deps",
    ])
    print(proc.stdout, proc.stderr, sep="\n")
    if not out.exists():
        print("pip-audit produced no report", file=sys.stderr)
        return 1
    report = json.loads(out.read_text() or "{}")
    deps = report.get("dependencies", report if isinstance(report, list) else [])
    vulnerable = [d for d in deps if d.get("vulns")]

    accepted, stale = _load_allowlist()
    unaccepted: list[str] = []
    print(f"audited {len(deps)} locked packages; {len(vulnerable)} with advisories")
    for d in vulnerable:
        for v in d["vulns"]:
            vid = v.get("id", "?")
            fix = ", ".join(v.get("fix_versions") or []) or "no fix published"
            line = f"  {d.get('name')}=={d.get('version')}: {vid} (fix: {fix})"
            if vid in accepted:
                print(f"{line}  [accepted]")
            else:
                print(f"{line}  [NOT ACCEPTED]")
                unaccepted.append(f"{d.get('name')} {vid}")

    for entry in stale:
        print(
            f"  allowlist entry {entry} is past its review_by date",
            file=sys.stderr,
        )

    if unaccepted and not args.allow_findings:
        print(
            f"\n{len(unaccepted)} advisory/advisories are not in "
            f"{ALLOWLIST.name}. Fix the dependency, or add an entry with a "
            f"reason and a review date.",
            file=sys.stderr,
        )
        return 1
    if stale and not args.allow_findings:
        print(
            f"\n{len(stale)} allowlist entry/entries are past review_by; "
            f"re-assess them or extend the date deliberately.",
            file=sys.stderr,
        )
        return 1
    return 0


# ── sbom ──

def cmd_sbom(args: argparse.Namespace) -> int:
    tool = _python_tool("cyclonedx-py", "cyclonedx_py")
    if not tool:
        print(
            f"cyclonedx-py is not installed in {sys.executable}: "
            f"{sys.executable} -m pip install cyclonedx-bom\n"
            "NO SBOM WAS PRODUCED — this is a failure, not a skip.",
            file=sys.stderr,
        )
        return 2
    out = Path(args.output) if args.output else _artifact_dir() / "sbom.cdx.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = _run([
        *tool, "requirements", str(LOCK),
        "--of", "JSON",
        "--sv", "1.5",
        # Strips serial numbers and timestamps, so the same lock yields a
        # byte-identical SBOM. Without it every build "changes" the SBOM and the
        # document stops being evidence of anything.
        "--output-reproducible",
        "-o", str(out),
    ])
    print(proc.stdout, proc.stderr, sep="\n")
    if proc.returncode != 0 or not out.exists():
        return proc.returncode or 1
    doc = json.loads(out.read_text())
    print(f"SBOM: {len(doc.get('components', []))} components -> {out}")
    return 0


# ── secrets ──

# Narrow, high-signal patterns. A broad entropy scanner over a repo carrying
# compliance corpora and eval fixtures produces noise nobody reads, and a
# control nobody reads is not a control.
_SECRET_PATTERNS = {
    "anthropic-key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "groq-key": re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b"),
    "google-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    # Only a database URL pointing at a REMOTE host is a credential leak. The
    # repo is full of local dev defaults (yggdrasil:yggdrasil@localhost) and
    # compose service names (corvus:corvus@db); flagging those trains people to
    # ignore this scanner, which is worse than not running it.
    "remote-db-url-with-password": re.compile(
        r"postgres(?:ql)?(?:\+\w+)?://[^\s:/@]+:[^\s:/@]{4,}@"
        r"(?!localhost|127\.0\.0\.1|db[:/]|postgres[:/]|\{)"
        r"[^\s:/@]+"
    ),
}

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "__pycache__", ".artifacts"}

# An inline, reviewable escape hatch. Deliberate fixtures — the honeypot canary
# in backend/tests/replay_auditor_honeypot.py is the motivating case — carry the
# pragma on the same line or the line above, so every suppression is visible in
# the diff that introduces it rather than buried in a baseline file.
_ALLOW_PRAGMA = "supply-chain: allow"


def _pragma_above(lines: list[str], lineno: int) -> bool:
    """True if the contiguous comment block directly above carries the pragma.

    Scanning the whole block, not just one line, so a suppression can be
    explained in prose. A one-line lookback silently failed on exactly the case
    this exists for: a two-line comment above the honeypot canary.
    """
    i = lineno - 2  # 0-indexed line directly above
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped.startswith("#"):
            return False
        if _ALLOW_PRAGMA in stripped:
            return True
        i -= 1
    return False


def _scan_tree(root: Path) -> list[tuple[str, str, int, str]]:
    findings = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _SKIP_DIRS & set(path.parts):
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue  # this file's own pattern definitions
        lines = text.split("\n")
        for lineno, line in enumerate(lines, 1):
            if _ALLOW_PRAGMA in line or _pragma_above(lines, lineno):
                continue
            for name, pattern in _SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(
                        (name, str(path.relative_to(root)), lineno, line.strip()[:120])
                    )
    return findings


def cmd_secrets(args: argparse.Namespace) -> int:
    findings = _scan_tree(REPO_ROOT)
    out = _artifact_dir() / "secret-scan.json"
    out.write_text(json.dumps(
        [{"rule": r, "file": f, "line": n, "excerpt": e} for r, f, n, e in findings],
        indent=2,
    ))
    if not findings:
        print(f"secret scan clean over {REPO_ROOT}")
        return 0
    for rule, file, line, excerpt in findings:
        print(f"  {rule}: {file}:{line}: {excerpt}", file=sys.stderr)
    print(f"{len(findings)} secret finding(s) -> {out}", file=sys.stderr)
    return 1


# ── artifact ──

# Criterion from roadmap record durability-reproducible-release: the production
# artifact must contain no personal credentials, no /root NVM assumptions, and no
# provider CLI path its runtime user cannot reach.
def cmd_artifact(args: argparse.Namespace) -> int:
    docker = _tool("docker")
    if not docker:
        print("docker is not available", file=sys.stderr)
        return 2
    image = args.image
    proc = _run([docker, "inspect", image])
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return 2
    meta = json.loads(proc.stdout)[0]
    config = meta.get("Config", {})
    env = {k: v for k, _, v in (e.partition("=") for e in config.get("Env") or [])}
    user = config.get("User") or "root"

    problems: list[str] = []

    if user in ("", "root", "0"):
        problems.append(f"image runs as {user!r}; expected an unprivileged user")

    for key, value in env.items():
        if "/root/" in value:
            problems.append(
                f"env {key} references /root ({value!r}) but the image runs as {user!r}"
            )
        for rule, pattern in _SECRET_PATTERNS.items():
            if pattern.search(value):
                problems.append(f"env {key} matches {rule}")
        if re.search(r"/home/(?!%s\b)[a-z][a-z0-9_-]*/" % re.escape(user), value):
            problems.append(
                f"env {key} embeds a home directory not belonging to {user!r}: {value!r}"
            )

    # A provider CLI path is only meaningful if it exists in the image AND the
    # runtime user can execute it. Baking a path to a binary that was never
    # installed is the exact defect this check exists to catch.
    cli_path = env.get("CLAUDE_CLI_PATH", "")
    if cli_path:
        probe = _run([
            docker, "run", "--rm", "--entrypoint", "/bin/sh", image,
            "-c", f'test -x "{cli_path}" && echo OK || echo MISSING',
        ])
        if "OK" not in probe.stdout:
            problems.append(
                f"CLAUDE_CLI_PATH={cli_path!r} is not an executable reachable by {user!r}"
            )

    report = {
        "image": image,
        "user": user,
        "env_keys": sorted(env),
        "labels": config.get("Labels") or {},
        "problems": problems,
    }
    out = _artifact_dir() / "artifact-scan.json"
    out.write_text(json.dumps(report, indent=2))

    if problems:
        for p in problems:
            print(f"  ARTIFACT: {p}", file=sys.stderr)
        print(f"{len(problems)} artifact problem(s) -> {out}", file=sys.stderr)
        return 1
    print(f"artifact scan clean: {image} (user={user}) -> {out}")
    return 0


# ── provenance ──

def cmd_provenance(args: argparse.Namespace) -> int:
    import hashlib

    def _git(*a: str) -> str:
        p = _run(["git", "-C", str(REPO_ROOT), *a])
        return p.stdout.strip() if p.returncode == 0 else "unknown"

    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    lock_bytes = LOCK.read_bytes()

    record = {
        "schema_version": 1,
        "artifact": args.image,
        "source": {
            "repository": _git("config", "--get", "remote.private.url"),
            "revision": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "materials": {
            "base_images": _FROM_DIGEST.findall(dockerfile),
            "dependency_lock": {
                "path": str(LOCK.relative_to(REPO_ROOT)),
                "sha256": hashlib.sha256(lock_bytes).hexdigest(),
                "pinned_packages": sum(
                    1 for line in lock_bytes.decode().split("\n")
                    if re.match(r"^[A-Za-z0-9]", line) and "==" in line
                ),
            },
            "dockerfile_sha256": hashlib.sha256(
                dockerfile.encode()).hexdigest(),
        },
        "builder": {
            "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
            "docker_version": _run(
                ["docker", "version", "--format", "{{.Server.Version}}"]
            ).stdout.strip() or "unknown",
        },
    }
    if args.image:
        p = _run(["docker", "inspect", "--format", "{{.Id}}", args.image])
        record["artifact_digest"] = p.stdout.strip() if p.returncode == 0 else None

    out = Path(args.output) if args.output else _artifact_dir() / "provenance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2))
    print(f"provenance -> {out}")
    print(json.dumps(record, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("audit", help="vulnerability audit of the locked deps")
    p.add_argument("--allow-findings", action="store_true")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("sbom", help="CycloneDX SBOM for the locked deps")
    p.add_argument("--output")
    p.set_defaults(func=cmd_sbom)

    p = sub.add_parser("secrets", help="credential scan of the source tree")
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser("artifact", help="inspect a built image")
    p.add_argument("image")
    p.set_defaults(func=cmd_artifact)

    p = sub.add_parser("provenance", help="emit a provenance record")
    p.add_argument("--image", default="")
    p.add_argument("--output")
    p.set_defaults(func=cmd_provenance)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
