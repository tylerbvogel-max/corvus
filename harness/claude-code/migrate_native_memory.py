#!/usr/bin/env python3
"""W6 Phase B: import Claude Code native memory files into Corvus-Mind.

One-time seeding migration (2026-07-12). Each detail file under the native
memory dir becomes a Mind lesson via POST /remember — the same staged
proposal → write gate → embed path every other lesson takes. Prescriptive
natives (type: feedback, plus hand-flagged policy files) enter at
`guidance` authority: they are months-proven and become the initial
charter. Descriptive natives enter at `informational` and must earn
promotion like everything else.

Idempotent: migrated labels are recorded in ~/.corvus-mind/native-migration.json
and skipped on re-run. MEMORY.md (the index) is NOT migrated — the charter
replaces it; index lines are projections, not facts.
"""

import json
import os
import sys
import urllib.request

BACKEND = "http://localhost:8005"
MEMORY_DIR = os.path.expanduser(
    "~/.claude/projects/-home-tylerbvogel/memory")
MARKER_PATH = os.path.expanduser("~/.corvus-mind/native-migration.json")
HTTP_TIMEOUT_S = 30
MAX_LESSON_CHARS = 7900  # /remember caps lesson at 8000

# (scope, project, authority) per file stem. `feedback` natives are
# prescriptive and months-proven -> guidance (initial charter membership).
# Descriptive project/user natives -> informational; they earn promotion.
OVERRIDES = {
    "feedback_auto_mode_questions": ("Harness", None, "guidance"),
    "feedback_auto_restart": ("Harness", None, "guidance"),
    "feedback_cache_refresh": ("Environment", None, "guidance"),
    "feedback_claude_cli_nested": ("Harness", None, "guidance"),
    "feedback_corvus_git": ("Projects", "corvus", "guidance"),
    "feedback_corvus_graph_testbed": ("Projects", "corvus", "guidance"),
    "feedback_corvus_llm_provider": ("Projects", "corvus", "guidance"),
    "feedback_corvus_quality_first_backend": ("Projects", "corvus", "guidance"),
    "feedback_corvus_roadmap_context": ("Projects", "master-corvus", "guidance"),
    "feedback_market_panel_no_paid_data": ("Projects", "market-panel", "guidance"),
    "feedback_master_corvus_git": ("Projects", "master-corvus", "guidance"),
    "project_corvus_singular_roadmap": ("Projects", "master-corvus", "guidance"),
    "project_aurora_ai_ops_and_corvus_strategy": ("User", None, "informational"),
    "project_career_origin_story": ("User", None, "informational"),
    "project_job_search_direction": ("User", None, "informational"),
    "project_master_corvus": ("Projects", "master-corvus", "informational"),
    "project_market_analytics_suite": ("Projects", "market-analytics-suite", "informational"),
}
DEFAULT_MAPPING = ("Projects", "corvus", "informational")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Minimal frontmatter parse: top-level `key: value` lines only."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def _load_marker() -> set:
    try:
        with open(MARKER_PATH, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def _save_marker(done: set) -> None:
    os.makedirs(os.path.dirname(MARKER_PATH), exist_ok=True)
    with open(MARKER_PATH, "w", encoding="utf-8") as fh:
        json.dump(sorted(done), fh, indent=2)


def _remember(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND}/remember", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    done = _load_marker()
    results = {"migrated": [], "skipped": [], "failed": []}
    for fname in sorted(os.listdir(MEMORY_DIR)):
        stem, ext = os.path.splitext(fname)
        if ext != ".md" or stem == "MEMORY":
            continue
        if stem in done:
            results["skipped"].append(stem)
            continue
        with open(os.path.join(MEMORY_DIR, fname), encoding="utf-8") as fh:
            meta, body = _parse_front_matter(fh.read())
        if not body.strip():
            results["skipped"].append(stem)
            continue
        scope, project, authority = OVERRIDES.get(stem, DEFAULT_MAPPING)
        label = (meta.get("name") or stem.replace("_", " "))[:200]
        payload = {
            "lesson": body[:MAX_LESSON_CHARS],
            "evidence": (f"Migrated 2026-07-12 from Claude Code native memory "
                         f"({fname}) — hand-curated across sessions and "
                         f"repeatedly load-bearing in practice."),
            "label": label,
            "scope": scope,
            "project": project,
            "node_type": "lesson",
            "abstraction_type": "principle",
            "summary": (meta.get("description") or body.split("\n")[0])[:500],
            "authority_level": authority,
        }
        try:
            out = _remember(payload)
        except (OSError, ValueError) as exc:
            results["failed"].append({"stem": stem, "error": str(exc)})
            continue
        done.add(stem)
        results["migrated"].append({
            "stem": stem, "scope": scope, "project": project,
            "authority": authority, "route": out.get("route"),
            "neuron_id": out.get("neuron_id")})
    _save_marker(done)
    print(json.dumps(results, indent=2))
    return 0 if not results["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
