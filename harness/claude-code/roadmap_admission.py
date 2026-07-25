"""Stdlib-only roadmap admission logic shared by coding-agent harnesses.

This is deliberately separate from ambient memory.  Memories are advisory and
fail-open; roadmap admission is deterministic, revision-pinned, and may block
material mutations in projects mapped to a Corvus-Mind ledger.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND = os.environ.get("CORVUS_MIND_BACKEND", "http://127.0.0.1:8005")
PLANNING_DIR = Path(
    os.environ.get(
        "CORVUS_MIND_PLANNING_DIR",
        os.path.expanduser("~/.corvus-mind/planning"),
    )
)
CACHE_PATH = PLANNING_DIR / "ledgers.json"
EPISODE_DIR = Path(
    os.environ.get(
        "CORVUS_MIND_EPISODE_DIR",
        os.path.expanduser("~/.corvus-mind/episodes"),
    )
)
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
HTTP_TIMEOUT_S = 0.45

DIRECT_MUTATION_TOOLS = {
    "apply_patch", "edit", "write", "multiedit", "notebookedit",
    "imagegen", "create_file", "delete_file", "move_file",
}
NON_MATERIAL_TOOLS = {
    "update_plan", "request_user_input", "view_image",
    "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource",
    "tool_search", "websearch", "webfetch", "recall", "roadmap_context",
    "get_goal",
}
MUTATING_TOOL_WORDS = re.compile(
    r"(?:^|__|_|-)(?:create|update|delete|remove|write|edit|patch|apply|commit|"
    r"push|deploy|restart|start|stop|send|post|put|accept|commission|remember|"
    r"forget|install|uninstall|revoke)(?:$|__|_|-)",
    re.IGNORECASE,
)
READ_TOOL_WORDS = re.compile(
    r"(?:^|__|_|-)(?:get|list|read|search|find|fetch|query|recall|inspect|"
    r"status|metrics|view|open)(?:$|__|_|-)",
    re.IGNORECASE,
)
SHELL_MUTATION = re.compile(
    r"(^|[;&|]\s*|\bsudo\s+)"
    r"(rm|rmdir|mv|cp|install|mkdir|touch|truncate|chmod|chown|ln|tee|"
    r"npm\s+(install|uninstall|publish)|npx\s+.*\binstall|"
    r"pip(?:3)?\s+install|apt(?:-get)?\s+|dnf\s+|yum\s+|"
    r"systemctl\s+.*\b(start|stop|restart|enable|disable|kill)\b|"
    r"git\s+(add|commit|push|pull|merge|rebase|reset|checkout|switch|"
    r"restore|clean|tag|branch\s+(-d|-D|--delete))\b|"
    r"psql\b.*\b(insert|update|delete|alter|drop|create|truncate)\b|"
    r"curl\b.*(?:\s-X\s*(POST|PUT|PATCH|DELETE)|--request\s*(POST|PUT|PATCH|DELETE)|"
    r"--data(?:-raw|-binary|-urlencode)?\b))",
    re.IGNORECASE,
)
SHELL_REDIRECT = re.compile(r"(^|[^<])>{1,2}(?!>)|<<?\s*[A-Za-z0-9_./~-]+")
SHELL_READ_ONLY = re.compile(
    r"^\s*(?:"
    r"pwd|ls|dir|find|fd|rg|grep|sed\s+-n|head|tail|cat|stat|file|wc|"
    r"which|whereis|command\s+-v|type\s+|realpath|readlink|"
    r"ps|ss|lsof|env\b|printenv|date|uname|id|whoami|"
    r"git\s+(status|diff|log|show|grep|rev-parse|remote\s+-v|branch(?:\s+--show-current)?)|"
    r"curl\s+(?:-sS?\s+|-I\s+|--head\s+)*https?://|"
    r"pytest\b|python(?:3)?\s+-m\s+pytest\b|"
    r"npm\s+(test|run\s+(?:test|lint|typecheck))\b|"
    r"npx\s+(?:tsc|eslint)\b"
    r")",
    re.IGNORECASE,
)
PATCH_PATH_RE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File:\s+(.+?)\s*$",
    re.MULTILINE,
)
ABSOLUTE_PROJECT_PATH_RE = re.compile(r"/home/[^/\s]+/Projects/[A-Za-z0-9_.-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def valid_session_id(value: Any) -> str:
    session_id = str(value or "unknown")
    return session_id if SESSION_ID_RE.fullmatch(session_id) else "unknown"


def load_cache() -> dict[str, Any] | None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("schema_version") != 1 or not isinstance(data.get("ledgers"), list):
        return None
    return data


def repair_cache(cwd: str | None = None) -> dict[str, Any] | None:
    # urllib is intentionally lazy: the steady-state path is a local cache
    # read, so every session should not pay network-client import cost.
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode({"cwd": cwd or ""})
    try:
        with urllib.request.urlopen(
            f"{BACKEND}/roadmap-ledgers/admission-context?{query}",
            timeout=HTTP_TIMEOUT_S,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    # The backend atomically writes the same document.  Keep a direct fallback
    # for isolated tests or deployments whose backend uses a different HOME.
    if not load_cache():
        try:
            PLANNING_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(
                json.dumps({
                    "schema_version": data["schema_version"],
                    "generated_at": data["generated_at"],
                    "ledgers": data["ledgers"],
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.chmod(CACHE_PATH, 0o600)
        except (OSError, KeyError):
            pass
    return load_cache() or data


def _normalize_path(value: str, cwd: str) -> str:
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd or os.getcwd(), expanded)
    return os.path.realpath(expanded)


def ledger_for_path(cache: dict[str, Any], path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    normalized = os.path.realpath(os.path.expanduser(path))
    matches = []
    for ledger in cache.get("ledgers", []):
        project_path = ledger.get("project_path")
        if not project_path:
            continue
        root = os.path.realpath(os.path.expanduser(project_path))
        if normalized == root or normalized.startswith(root.rstrip(os.sep) + os.sep):
            matches.append((len(root), ledger))
    return max(matches, default=(0, None), key=lambda item: item[0])[1]


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input")
    return value if isinstance(value, dict) else {"_raw": value}


def target_paths(payload: dict[str, Any]) -> list[str]:
    cwd = str(payload.get("cwd") or os.getcwd())
    tool_input = _tool_input(payload)
    paths: list[str] = []
    for key in ("workdir", "cwd", "file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(_normalize_path(value.strip(), cwd))

    raw_parts = [
        tool_input.get("command"),
        tool_input.get("patch"),
        tool_input.get("input"),
        tool_input.get("_raw"),
    ]
    for raw in raw_parts:
        if not isinstance(raw, str):
            continue
        for patch_path in PATCH_PATH_RE.findall(raw):
            paths.append(_normalize_path(patch_path.strip(), cwd))
        for project_path in ABSOLUTE_PROJECT_PATH_RE.findall(raw):
            paths.append(os.path.realpath(project_path))
    paths.append(os.path.realpath(cwd))
    # Preserve order while avoiding repeated resolution work.
    return list(dict.fromkeys(paths))


def resolve_ledger(cache: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    matches = [
        ledger_for_path(cache, path)
        for path in target_paths(payload)
    ]
    serialized_input = json.dumps(_tool_input(payload), ensure_ascii=False)
    for ledger in cache.get("ledgers", []):
        project_path = ledger.get("project_path")
        if project_path and project_path in serialized_input:
            matches.append(ledger)
    matches = [item for item in matches if item is not None]
    if not matches:
        return None
    # A tool spanning two mapped projects is materially ambiguous; choosing
    # either would hide that fact.  The gate reports the first deterministic
    # mapping and the caller can split the operation.
    slugs = {item["slug"] for item in matches}
    if len(slugs) > 1:
        return {
            **matches[0],
            "_ambiguous_slugs": sorted(slugs),
        }
    return matches[0]


def _shell_is_material(command: str) -> bool:
    command = command.strip()
    if not command:
        return False
    if SHELL_MUTATION.search(command) or SHELL_REDIRECT.search(command):
        return True
    # Multiple shell segments are read-only only when every segment is
    # independently recognizable.  Unknown commands fail toward admission.
    segments = [
        item.strip()
        for item in re.split(r"\s*(?:&&|\|\||;|\n)\s*", command)
        if item.strip()
    ]
    return not segments or any(not SHELL_READ_ONLY.match(item) for item in segments)


def is_material_tool(payload: dict[str, Any]) -> bool:
    name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
    short = name.rsplit("__", 1)[-1].lower()
    if (
        short in NON_MATERIAL_TOOLS
        or "roadmap_admit" in name.lower()
        or "planning_admit" in name.lower()
    ):
        return False
    if short in DIRECT_MUTATION_TOOLS:
        return True
    if short in {"bash", "exec_command", "shell"}:
        command = str(_tool_input(payload).get("command") or "")
        return _shell_is_material(command)
    if MUTATING_TOOL_WORDS.search(name):
        return True
    if READ_TOOL_WORDS.search(name):
        return False
    # Unknown local tools may mutate.  Requiring admission in mapped projects
    # is safer than silently creating a new bypass whenever a harness adds one.
    return True


def _episode_events(session_id: str) -> list[dict[str, Any]]:
    path = EPISODE_DIR / f"{valid_session_id(session_id)}.jsonl"
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def latest_admission(session_id: str, slug: str) -> dict[str, Any] | None:
    return next(
        (
            event for event in reversed(_episode_events(session_id))
            if event.get("event") == "PlanningAdmission"
            and event.get("ledger_slug") == slug
        ),
        None,
    )


def append_event(session_id: str, event: dict[str, Any]) -> None:
    session_id = valid_session_id(session_id)
    EPISODE_DIR.mkdir(parents=True, exist_ok=True)
    path = EPISODE_DIR / f"{session_id}.jsonl"
    record = {"ts": _now(), "session_id": session_id, **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_pending(
    session_id: str,
    cwd: str,
    ledger: dict[str, Any] | None,
    *,
    harness: str,
) -> None:
    prior = _episode_events(session_id)
    if any(event.get("event") == "PlanningAdmissionPending" for event in prior):
        return
    append_event(
        session_id,
        {
            "event": "PlanningAdmissionPending",
            "cwd": cwd,
            "harness": harness,
            "ledger_slug": ledger.get("slug") if ledger else None,
            "ledger_revision": ledger.get("revision") if ledger else None,
            "resolution": "cwd" if ledger else "mutation-target",
        },
    )


def format_session_context(
    cache: dict[str, Any] | None,
    *,
    session_id: str,
    cwd: str,
) -> str:
    ledgers = cache.get("ledgers", []) if cache else []
    ledger = ledger_for_path(cache, cwd) if cache else None
    lines = [
        "ROADMAP ADMISSION POLICY (enforced by Corvus-Mind)",
        f"Session: {session_id}",
    ]
    if ledger:
        summary = ledger.get("summary", {})
        lines.extend([
            (
                f"Mapped ledger: {ledger['name']} ({ledger['slug']}) "
                f"@ revision {ledger['revision']} / state {ledger.get('state_version')}"
            ),
            (
                f"Portfolio: {summary.get('moving', 0)} moving · "
                f"{summary.get('reviews_due', 0)} reviews due · "
                f"{summary.get('completion', 0)}% complete"
            ),
            "Candidate records:",
        ])
        for candidate in ledger.get("candidates", []):
            flags = [
                str(candidate.get("status") or "unknown"),
                str(candidate.get("horizon") or "unclassified"),
            ]
            if candidate.get("ready"):
                flags.append("ready")
            if candidate.get("review_status") == "due":
                flags.append("review due")
            lines.append(
                f"- {candidate['id']} — {candidate['label']} "
                f"[{'; '.join(flags)}]"
            )
    elif ledgers:
        names = ", ".join(f"{item['slug']} → {item.get('project_path') or 'unmapped'}"
                          for item in ledgers[:6])
        lines.extend([
            "Startup cwd is not mapped; the gate will resolve the ledger from "
            "the first mutation's workdir or target path.",
            f"Known ledgers: {names}",
        ])
    else:
        lines.extend([
            "The local ledger projection is unavailable. Corvus-Mind will retry "
            "resolution before mapped mutations; do not assume absence means approval.",
        ])
    lines.extend([
        "Before any material mutation in a mapped project, call the "
        "`roadmap_admit` Corvus-Mind tool with this session id, the ledger slug, "
        "and an unfinished record id. If the work is intentionally outside the "
        "roadmap, use mode `off-ledger` with a concrete reason.",
        "Read-only discovery is allowed before admission. Admissions are pinned "
        "to the ledger revision; a changed revision requires readmission.",
    ])
    return "\n".join(lines)


def gate(payload: dict[str, Any], cache: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not is_material_tool(payload):
        return None
    cache = cache or load_cache() or repair_cache(str(payload.get("cwd") or ""))
    if not cache:
        return None
    ledger = resolve_ledger(cache, payload)
    if ledger is None:
        return None
    session_id = valid_session_id(payload.get("session_id"))
    ambiguous = ledger.get("_ambiguous_slugs")
    if ambiguous:
        return {
            "decision": "block",
            "reason": (
                "Roadmap admission blocked a mutation spanning multiple mapped "
                f"projects ({', '.join(ambiguous)}). Split the operation and admit "
                "each project deliberately."
            ),
            "ledger": ledger,
        }
    admission = latest_admission(session_id, ledger["slug"])
    if admission is None:
        return {
            "decision": "block",
            "reason": (
                f"Roadmap admission required for {ledger['name']} "
                f"({ledger['slug']} @ revision {ledger['revision']}). "
                f"Call roadmap_admit(session_id={session_id!r}, "
                f"ledger_slug={ledger['slug']!r}, record_id='<unfinished-record>') "
                "or use mode='off-ledger' with a concrete reason."
            ),
            "ledger": ledger,
        }
    admitted_revision = admission.get("ledger_revision")
    if admitted_revision != ledger.get("revision"):
        return {
            "decision": "block",
            "reason": (
                f"Roadmap admission is stale: session {session_id} was admitted "
                f"to {ledger['slug']} revision {admitted_revision}, but the current "
                f"revision is {ledger['revision']}. Re-run roadmap_admit before mutation."
            ),
            "ledger": ledger,
        }
    return None


def planning_return(session_id: str) -> dict[str, Any] | None:
    """Append one reconciliation receipt for material work since the last stop."""
    events = _episode_events(session_id)
    last_return_index = max(
        (index for index, event in enumerate(events)
         if event.get("event") == "PlanningReturn"),
        default=-1,
    )
    admissions = [
        (index, event) for index, event in enumerate(events)
        if index > last_return_index and event.get("event") == "PlanningAdmission"
    ]
    if not admissions:
        return None
    admission_index, admission = admissions[-1]
    material = []
    for event in events[admission_index + 1:]:
        if event.get("event") != "PostToolUse" or not event.get("ok", True):
            continue
        synthetic = {
            "tool_name": event.get("tool"),
            "tool_input": event.get("input") or {},
        }
        if is_material_tool(synthetic):
            material.append(event)
    if not material:
        return None
    receipt = {
        "event": "PlanningReturn",
        "ledger_slug": admission.get("ledger_slug"),
        "ledger_revision": admission.get("ledger_revision"),
        "record_id": admission.get("record_id"),
        "record_label": admission.get("record_label"),
        "admission_mode": admission.get("mode"),
        "material_tool_count": len(material),
        "tools": sorted({str(event.get("tool")) for event in material}),
        "status": "reconcile-required",
        "note": (
            "Execution evidence was captured; durable roadmap status remains "
            "unchanged until explicit review."
        ),
    }
    append_event(session_id, receipt)
    return receipt
