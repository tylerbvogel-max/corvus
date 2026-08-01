#!/usr/bin/env python3
"""Compare the hand-authored architecture manifest against derived ground truth.

extract_architecture.py says what the code does. architecture/manifest.yaml says
what its owner believes. This script reports every place those disagree, and that
report is the actual deliverable — the diagram is a byproduct.

Four classes of finding, roughly in order of how much they should worry you:

  UNCLASSIFIED  a module exists that no box claims. This is drift: code grew in a
                place the mental model has no name for.
  PHANTOM       a box was declared that matches no modules at all. The model
                contains a component that isn't there (or isn't there yet).
  VIOLATION     a stated invariant is contradicted by the import/call graph.
  PATH_DRIFT    a declared end-to-end path skips, invents, or reorders a hop
                relative to what the call graph actually supports.

Requires PyYAML, which lives in the backend venv rather than system python:

    backend/venv/bin/python backend/scripts/check_architecture.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    sys.stderr.write(
        "PyYAML not found. Run this with the backend venv interpreter:\n"
        "  backend/venv/bin/python backend/scripts/check_architecture.py\n"
    )
    raise SystemExit(2)

SCHEMA_VERSION = 4
VALID_TIERS = {"hot", "background", "governance", "interface", "eval", "infra"}


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a path glob to a regex. `**` spans separators, `*` does not."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


class Classifier:
    """Assigns modules to boxes. First matching box wins, in declaration order,
    so a specific box listed before a catch-all does the right thing."""

    def __init__(self, boxes: list[dict]):
        self.boxes = boxes
        self.compiled = [
            (b["id"], [glob_to_regex(p) for p in (b.get("owns") or [])])
            for b in boxes
        ]

    def box_for(self, path: str) -> str | None:
        for box_id, patterns in self.compiled:
            if any(p.match(path) for p in patterns):
                return box_id
        return None


def load_manifest(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    boxes = [b for b in (data.get("boxes") or []) if b.get("id") not in (None, "TODO")]
    invariants = [i for i in (data.get("invariants") or []) if i.get("id") not in (None, "TODO")]
    processes = [
        p for p in (data.get("processes") or data.get("paths") or [])
        if p.get("id") not in (None, "TODO")
    ]
    return {
        "boxes": boxes,
        "invariants": invariants,
        "processes": processes,
        "memory_engine": data.get("memory_engine") or {},
        "system_context": data.get("system_context") or {},
        "bounded_contexts": data.get("bounded_contexts") or [],
        "deployments": data.get("deployments") or [],
        "decisions": data.get("decisions") or [],
        "runtime_nodes": data.get("runtime_nodes") or [],
        "runtime_connections": data.get("runtime_connections") or [],
        "raw": data,
    }


def validate_manifest(m: dict) -> list[str]:
    problems = []

    def required_string(owner: str, value) -> None:
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{owner}: non-empty string is required")

    if m["raw"].get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version: expected {SCHEMA_VERSION}, "
            f"got {m['raw'].get('schema_version')!r}"
        )

    summary = m["raw"].get("system_summary") or {}
    for field in ("identity", "primary_loop", "governing_principle"):
        required_string(f"system_summary.{field}", summary.get(field))
    tenants = summary.get("current_tenants")
    if not isinstance(tenants, list) or not tenants:
        problems.append("system_summary.current_tenants: non-empty list is required")
    elif any(not isinstance(tenant, str) or not tenant.strip() for tenant in tenants):
        problems.append("system_summary.current_tenants: every entry must be a non-empty string")

    memory_engine = m["memory_engine"]
    for field in ("title", "summary"):
        required_string(f"memory_engine.{field}", memory_engine.get(field))
    flow = memory_engine.get("flow")
    if not isinstance(flow, list) or not flow:
        problems.append("memory_engine.flow: non-empty list is required")
    elif any(not isinstance(item, str) or not item.strip() for item in flow):
        problems.append("memory_engine.flow: every entry must be a non-empty string")
    zone_ids = set()
    for zone in memory_engine.get("zones") or []:
        zone_id = zone.get("id")
        if zone_id in zone_ids:
            problems.append(f"duplicate memory-engine zone id: {zone_id}")
        zone_ids.add(zone_id)
        for field in ("name", "boundary", "purpose"):
            required_string(f"memory-engine zone {zone_id}.{field}", zone.get(field))
        details = zone.get("details")
        if not isinstance(details, list) or not details:
            problems.append(f"memory-engine zone {zone_id}: details are required")
        elif any(not isinstance(item, str) or not item.strip() for item in details):
            problems.append(
                f"memory-engine zone {zone_id}: every detail must be a non-empty string"
            )
        if not zone.get("evidence"):
            problems.append(f"memory-engine zone {zone_id}: evidence is required")
    if not zone_ids:
        problems.append("memory_engine.zones: at least one zone is required")
    note_ids = set()
    for note in memory_engine.get("truth_notes") or []:
        note_id = note.get("id")
        if note_id in note_ids:
            problems.append(f"duplicate memory-engine truth-note id: {note_id}")
        note_ids.add(note_id)
        for field in ("label", "value"):
            required_string(f"memory-engine truth-note {note_id}.{field}", note.get(field))
        if not note.get("evidence"):
            problems.append(f"memory-engine truth-note {note_id}: evidence is required")
    if not note_ids:
        problems.append("memory_engine.truth_notes: at least one truth note is required")

    seen = set()
    for b in m["boxes"]:
        if b["id"] in seen:
            problems.append(f"duplicate box id: {b['id']}")
        seen.add(b["id"])
        tier = b.get("tier")
        if tier not in VALID_TIERS:
            problems.append(f"box {b['id']}: tier {tier!r} not one of {sorted(VALID_TIERS)}")
        if not b.get("owns"):
            problems.append(f"box {b['id']}: no `owns` patterns")
    for inv in m["invariants"]:
        chk = inv.get("check")
        if chk and not isinstance(chk, dict):
            problems.append(f"invariant {inv['id']}: `check` must be a mapping or null")
    process_ids = set()
    box_ids = {b["id"] for b in m["boxes"]}
    for process in m["processes"]:
        process_id = process.get("id")
        if process_id in process_ids:
            problems.append(f"duplicate process id: {process_id}")
        process_ids.add(process_id)
        for field in ("name", "availability", "trigger", "cadence", "why", "outcome"):
            if not isinstance(process.get(field), str) or not process[field].strip():
                problems.append(f"process {process_id}: {field} is required")
        through = process.get("through")
        if not isinstance(through, list) or not through:
            problems.append(f"process {process_id}: through component path is required")
        else:
            for component in through:
                if component not in box_ids:
                    problems.append(
                        f"process {process_id}: unknown through component {component!r}"
                    )
        steps = process.get("steps") or []
        if not steps:
            problems.append(f"process {process_id}: at least one step is required")
        for index, step in enumerate(steps):
            component = step.get("component")
            if component not in box_ids:
                problems.append(
                    f"process {process_id} step {index + 1}: unknown component {component!r}"
                )
            for field in ("actor", "does", "state_change", "guardrail"):
                if not isinstance(step.get(field), str) or not step[field].strip():
                    problems.append(
                        f"process {process_id} step {index + 1}: {field} is required"
                    )
            if not step.get("evidence"):
                problems.append(f"process {process_id} step {index + 1}: evidence is required")
    runtime_ids = set()
    for node in m["runtime_nodes"]:
        node_id = node.get("id")
        if node_id in runtime_ids:
            problems.append(f"duplicate runtime node id: {node_id}")
        runtime_ids.add(node_id)
        for field in ("name", "kind", "status", "when", "purpose", "technology"):
            if not isinstance(node.get(field), str) or not node[field].strip():
                problems.append(f"runtime node {node_id}: {field} is required")
        if not node.get("evidence"):
            problems.append(f"runtime node {node_id}: evidence is required")
    for connection in m["runtime_connections"]:
        if connection.get("from") not in runtime_ids:
            problems.append(
                f"runtime connection {connection.get('from')} -> {connection.get('to')}: "
                "unknown source"
            )
        if connection.get("to") not in runtime_ids:
            problems.append(
                f"runtime connection {connection.get('from')} -> {connection.get('to')}: "
                "unknown target"
            )

    system_context = m["system_context"]
    system = system_context.get("system") or {}
    for field in ("id", "name", "purpose", "boundary"):
        required_string(f"system_context.system.{field}", system.get(field))
    if not system.get("evidence"):
        problems.append("system_context.system: evidence is required")
    participant_ids = set()
    for participant in system_context.get("participants") or []:
        participant_id = participant.get("id")
        if participant_id in participant_ids:
            problems.append(f"duplicate system-context participant id: {participant_id}")
        participant_ids.add(participant_id)
        for field in ("name", "kind", "role", "trust"):
            required_string(
                f"system-context participant {participant_id}.{field}",
                participant.get(field),
            )
        if not participant.get("evidence"):
            problems.append(
                f"system-context participant {participant_id}: evidence is required"
            )
    context_nodes = participant_ids | ({system.get("id")} if system.get("id") else set())
    for relationship in system_context.get("relationships") or []:
        source = relationship.get("from")
        target = relationship.get("to")
        if source not in context_nodes or target not in context_nodes:
            problems.append(
                f"system-context relationship {source} -> {target}: unknown endpoint"
            )
        for field in ("label", "when", "data"):
            required_string(
                f"system-context relationship {source} -> {target}.{field}",
                relationship.get(field),
            )

    bounded_ids = set()
    for context in m["bounded_contexts"]:
        context_id = context.get("id")
        if context_id in bounded_ids:
            problems.append(f"duplicate bounded context id: {context_id}")
        bounded_ids.add(context_id)
        for field in ("name", "kind", "purpose", "ownership"):
            required_string(f"bounded context {context_id}.{field}", context.get(field))
        for field in ("concepts", "canonical_state", "invariants", "components", "evidence"):
            if not isinstance(context.get(field), list) or not context[field]:
                problems.append(f"bounded context {context_id}: {field} is required")
        for component in context.get("components") or []:
            if component not in box_ids:
                problems.append(
                    f"bounded context {context_id}: unknown component {component!r}"
                )
    for context in m["bounded_contexts"]:
        for integration in context.get("integrates_with") or []:
            target = integration.get("context")
            if target not in bounded_ids:
                problems.append(
                    f"bounded context {context.get('id')}: unknown integration {target!r}"
                )
            required_string(
                f"bounded context {context.get('id')} -> {target}.contract",
                integration.get("contract"),
            )

    deployment_ids = set()
    for deployment in m["deployments"]:
        deployment_id = deployment.get("id")
        if deployment_id in deployment_ids:
            problems.append(f"duplicate deployment id: {deployment_id}")
        deployment_ids.add(deployment_id)
        for field in ("name", "status", "purpose", "trust_boundary"):
            required_string(
                f"deployment {deployment_id}.{field}", deployment.get(field)
            )
        for field in ("nodes", "failure_modes", "evidence"):
            if not isinstance(deployment.get(field), list) or not deployment[field]:
                problems.append(f"deployment {deployment_id}: {field} is required")
        for node in deployment.get("nodes") or []:
            if node not in runtime_ids:
                problems.append(
                    f"deployment {deployment_id}: unknown runtime node {node!r}"
                )
        for index, failure in enumerate(deployment.get("failure_modes") or []):
            for field in ("failure", "visible_as", "recovery"):
                required_string(
                    f"deployment {deployment_id} failure {index + 1}.{field}",
                    failure.get(field),
                )

    decision_ids = set()
    for decision in m["decisions"]:
        decision_id = decision.get("id")
        if decision_id in decision_ids:
            problems.append(f"duplicate decision id: {decision_id}")
        decision_ids.add(decision_id)
        for field in (
            "title", "status", "recorded", "context", "decision", "record",
        ):
            required_string(f"decision {decision_id}.{field}", decision.get(field))
        for field in ("consequences", "alternatives", "evidence"):
            if not isinstance(decision.get(field), list) or not decision[field]:
                problems.append(f"decision {decision_id}: {field} is required")
        for context_id in decision.get("bounded_contexts") or []:
            if context_id not in bounded_ids:
                problems.append(
                    f"decision {decision_id}: unknown bounded context {context_id!r}"
                )
    return problems


# --------------------------------------------------------------------------
# invariant checks
# --------------------------------------------------------------------------

def resolve_scope(spec: str, boxes: dict[str, list[str]], modules: dict) -> set[str]:
    """A scope is either a box id or a path glob; both resolve to module names."""
    if spec in boxes:
        return set(boxes[spec])
    rx = glob_to_regex(spec)
    return {m for m, info in modules.items() if rx.match(info["path"])}


def check_no_import(cfg, boxes, arch) -> list[dict]:
    modules = arch["modules"]
    src = resolve_scope(cfg["from"], boxes, modules)
    dst = resolve_scope(cfg["to"], boxes, modules)
    hits = []
    for m in sorted(src):
        for target in modules[m]["imports"]:
            if target in dst:
                hits.append({"module": m, "file": modules[m]["path"], "imports": target})
    return hits


def check_sole_writer(cfg, boxes, arch) -> list[dict]:
    modules = arch["modules"]
    allowed = resolve_scope(cfg["box"], boxes, modules)
    # Tests construct models freely and legitimately; without an exemption this
    # check reports the test suite as a write-path violation, which is noise
    # that would train you to ignore the rule.
    for scope in cfg.get("except") or []:
        allowed |= resolve_scope(scope, boxes, modules)
    model = cfg["table"]
    target = model.lower().rstrip("s")
    hits = []
    for m, info in modules.items():
        if m in allowed:
            continue
        for w in info["db_writes"]:
            got = (w.get("model") or "")
            if got and got.lower().rstrip("s") == target:
                hits.append({
                    "module": m, "file": info["path"], "line": w["line"],
                    "in": w["in"], "kind": w["kind"], "model": got,
                })
    return hits


def check_no_llm(cfg, boxes, arch) -> list[dict]:
    modules = arch["modules"]
    scope = resolve_scope(cfg["box"], boxes, modules)
    hits = []
    for m in sorted(scope):
        for site in modules[m]["llm_sites"]:
            hits.append({
                "module": m, "file": modules[m]["path"],
                "line": site["line"], "in": site["in"], "kind": site["kind"],
            })
    return hits


def check_no_cycles(cfg, boxes, arch) -> list[dict]:
    modules = arch["modules"]
    scope = resolve_scope(cfg["box"], boxes, modules)
    return [
        {"cycle": cyc}
        for cyc in arch.get("import_cycles", [])
        if any(node in scope for node in cyc)
    ]


def check_entrypoint(cfg, boxes, arch) -> list[dict]:
    modules = arch["modules"]
    scope = resolve_scope(cfg["box"], boxes, modules)
    allowed = set()
    for b in cfg.get("only_from") or []:
        allowed |= resolve_scope(b, boxes, modules)
    allowed |= scope
    hits = []
    for target in sorted(scope):
        for caller in arch.get("imported_by", {}).get(target, []):
            if caller not in allowed:
                hits.append({
                    "module": caller, "file": modules.get(caller, {}).get("path", "?"),
                    "reaches": target,
                })
    return hits


def check_must_reach(cfg, boxes, arch) -> list[dict]:
    """Obligation, not prohibition: every module in `from` must transitively
    reach at least one module in `to`.

    The other five checks all forbid something. This one requires something, and
    it exists because the most important rule in this codebase is of that shape:
    a scoring path that cannot reach the cooling path reproduces a failure that
    already shipped once. Traversal unions import and call edges — an obligation
    should be generous about how the dependency is realized, unlike a
    prohibition, which should be strict.
    """
    modules = arch["modules"]
    src = resolve_scope(cfg["from"], boxes, modules)
    dst = resolve_scope(cfg["to"], boxes, modules)
    if not dst:
        return [{"module": "<none>", "file": cfg["to"], "note": "target scope matched no modules"}]

    edges: dict[str, set[str]] = defaultdict(set)
    for m, info in modules.items():
        edges[m] |= set(info["imports"])
    for m, targets in (arch.get("call_edges") or {}).items():
        edges[m] |= set(targets)

    hits = []
    for m in sorted(src):
        seen, queue = {m}, [m]
        found = False
        while queue and not found:
            cur = queue.pop()
            for nxt in edges.get(cur, ()):
                if nxt in dst:
                    found = True
                    break
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if not found:
            hits.append({"module": m, "file": modules[m]["path"],
                         "note": f"never reaches {cfg['to']}"})
    return hits


CHECKS = {
    "must_reach": check_must_reach,
    "no_import": check_no_import,
    "sole_writer": check_sole_writer,
    "no_llm": check_no_llm,
    "no_cycles": check_no_cycles,
    "entrypoint": check_entrypoint,
}


# --------------------------------------------------------------------------
# path checks
# --------------------------------------------------------------------------

def _split_evidence(value: str) -> tuple[str, str | None]:
    path, marker, symbol = str(value).partition("::")
    return path, symbol if marker and symbol else None


def check_processes(manifest, classifier: Classifier, arch, root: Path) -> list[dict]:
    """Validate every authored process step against the extracted inventory.

    Static analysis cannot prove business intent or runtime ordering. It can prove
    the less glamorous facts that keep an atlas honest: the cited file exists, an
    optional cited Python symbol exists, and the evidence belongs to the component
    the step says performs the work.
    """
    modules = arch["modules"]
    modules_by_path = {info["path"]: info for info in modules.values()}
    results = []
    for process in manifest["processes"]:
        checked_steps = []
        error_count = 0
        for index, step in enumerate(process.get("steps") or []):
            evidence_results = []
            for raw in step.get("evidence") or []:
                path, symbol = _split_evidence(raw)
                exists = (root / path).is_file()
                actual_component = classifier.box_for(path)
                component_ok = actual_component == step.get("component")
                symbol_ok = None
                if symbol and path in modules_by_path:
                    functions = modules_by_path[path].get("functions") or {}
                    symbol_ok = symbol in functions or any(
                        name.endswith(f".{symbol}") for name in functions
                    )
                elif symbol:
                    symbol_ok = False
                ok = exists and component_ok and symbol_ok is not False
                error_count += int(not ok)
                evidence_results.append({
                    "evidence": raw,
                    "exists": exists,
                    "actual_component": actual_component,
                    "component_ok": component_ok,
                    "symbol_ok": symbol_ok,
                    "ok": ok,
                })
            checked_steps.append({
                **step,
                "index": index + 1,
                "evidence_results": evidence_results,
                "evidence_ok": bool(evidence_results)
                and all(item["ok"] for item in evidence_results),
            })
        results.append({
            **process,
            "steps": checked_steps,
            "evidence_errors": error_count,
            "evidence_ok": error_count == 0,
        })
    return results


def check_runtime(manifest, root: Path) -> list[dict]:
    """Validate file evidence attached to runtime nodes."""
    out = []
    for node in manifest["runtime_nodes"]:
        checks = []
        for raw in node.get("evidence") or []:
            path, _symbol = _split_evidence(raw)
            checks.append({"evidence": raw, "exists": (root / path).is_file()})
        out.append({
            **node,
            "evidence_results": checks,
            "evidence_ok": bool(checks) and all(c["exists"] for c in checks),
        })
    return out


def check_authored_evidence(
    items: list[dict],
    root: Path,
    *,
    extra_path_field: str | None = None,
) -> list[dict]:
    """Attach existence receipts to authored architecture records."""
    out = []
    for item in items:
        raw_paths = list(item.get("evidence") or [])
        if extra_path_field and item.get(extra_path_field):
            raw_paths.append(item[extra_path_field])
        checks = []
        for raw in raw_paths:
            path, _symbol = _split_evidence(raw)
            checks.append({"evidence": raw, "exists": (root / path).is_file()})
        out.append({
            **item,
            "evidence_results": checks,
            "evidence_ok": bool(checks) and all(check["exists"] for check in checks),
        })
    return out


def check_freshness(arch: dict, root: Path) -> dict:
    """Compare the committed extraction with the current extracted source set."""
    try:
        from extract_architecture import source_fingerprint
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from extract_architecture import source_fingerprint

    current = source_fingerprint(root)
    stored = arch.get("source_inventory") or {}
    return {
        "fresh": bool(stored.get("fingerprint"))
        and stored.get("fingerprint") == current["fingerprint"],
        "stored_fingerprint": stored.get("fingerprint"),
        "current_fingerprint": current["fingerprint"],
        "stored_files": stored.get("files"),
        "current_files": current["files"],
    }


# --------------------------------------------------------------------------

def run(arch_path: Path, manifest_path: Path, root: Path | None = None) -> dict:
    arch = json.loads(arch_path.read_text(encoding="utf-8"))
    manifest = load_manifest(manifest_path)
    problems = validate_manifest(manifest)
    root = root or manifest_path.resolve().parent.parent

    classifier = Classifier(manifest["boxes"])
    modules = arch["modules"]

    boxes: dict[str, list[str]] = defaultdict(list)
    unclassified = []
    for name, info in modules.items():
        box = classifier.box_for(info["path"])
        if box:
            boxes[box].append(name)
        else:
            unclassified.append({"module": name, "file": info["path"], "loc": info["loc"],
                                 "lang": "py"})

    # Frontend files classify into the same boxes. They carry no import/call
    # analysis of the kind invariants run on, so they are tracked separately for
    # totals — a box owning only TypeScript is still a real box, but no
    # import-graph invariant will ever fire on it.
    frontend_boxes: dict[str, list[str]] = defaultdict(list)
    fe = arch.get("frontend") or {}
    fe_files = fe.get("files", {}) if fe.get("available") else {}
    for rel, f in fe_files.items():
        box = classifier.box_for(rel)
        if box:
            frontend_boxes[box].append(rel)
        else:
            unclassified.append({"module": rel, "file": rel, "loc": f["loc"], "lang": "ts"})

    phantom = [
        b["id"] for b in manifest["boxes"]
        if not boxes.get(b["id"]) and not frontend_boxes.get(b["id"])
    ]

    violations = []
    unchecked = []
    for inv in manifest["invariants"]:
        chk = inv.get("check")
        if not chk:
            unchecked.append({"id": inv["id"], "statement": inv.get("statement")})
            continue
        for kind, cfg in chk.items():
            fn = CHECKS.get(kind)
            if not fn:
                problems.append(f"invariant {inv['id']}: unknown check {kind!r}")
                continue
            hits = fn(cfg, boxes, arch)
            if hits:
                baseline_count = int(inv.get("baseline_count") or 0)
                violations.append({
                    "id": inv["id"], "statement": inv.get("statement"),
                    "check": kind, "count": len(hits), "hits": hits[:40],
                    "truncated": max(0, len(hits) - 40),
                    "baseline_count": baseline_count,
                    "regression_count": max(0, len(hits) - baseline_count),
                })

    box_summary = []
    for b in manifest["boxes"]:
        members = sorted(boxes.get(b["id"], []))
        fe_members = sorted(frontend_boxes.get(b["id"], []))
        box_summary.append({
            "id": b["id"], "name": b.get("name"), "tier": b.get("tier"),
            "purpose": b.get("purpose"),
            "confidence": b.get("confidence"),
            "module_count": len(members),
            "frontend_file_count": len(fe_members),
            "loc": sum(modules[m]["loc"] for m in members)
                   + sum(fe_files[f]["loc"] for f in fe_members),
            "routes": sum(len(modules[m]["routes"]) for m in members),
            "modules": members,
            "frontend_files": fe_members,
        })

    unclassified.sort(key=lambda u: -u["loc"])
    processes = check_processes(manifest, classifier, arch, root)
    runtime_nodes = check_runtime(manifest, root)
    system_context = {
        **manifest["system_context"],
        "system": check_authored_evidence(
            [manifest["system_context"].get("system") or {}], root
        )[0],
        "participants": check_authored_evidence(
            manifest["system_context"].get("participants") or [], root
        ),
    }
    bounded_contexts = check_authored_evidence(
        manifest["bounded_contexts"], root
    )
    raw_memory_engine = manifest["memory_engine"]
    memory_engine = {
        **raw_memory_engine,
        "zones": check_authored_evidence(raw_memory_engine.get("zones") or [], root),
        "truth_notes": check_authored_evidence(
            raw_memory_engine.get("truth_notes") or [], root
        ),
    }
    deployments = check_authored_evidence(manifest["deployments"], root)
    decisions = check_authored_evidence(
        manifest["decisions"], root, extra_path_field="record"
    )
    freshness = check_freshness(arch, root)
    process_evidence_errors = sum(p["evidence_errors"] for p in processes)
    authored_evidence_errors = sum(
        int(not item["evidence_ok"])
        for group in (
            [system_context["system"]],
            system_context["participants"],
            memory_engine["zones"],
            memory_engine["truth_notes"],
            bounded_contexts,
            deployments,
            decisions,
        )
        for item in group
    )
    fitness_regressions = sum(v["regression_count"] for v in violations)

    return {
        "schema_version": SCHEMA_VERSION,
        "review": manifest["raw"].get("review") or {},
        "system_summary": manifest["raw"].get("system_summary") or {},
        "totals": {
            "modules": len(modules),
            "frontend_files": len(fe_files),
            "units": len(modules) + len(fe_files),
            "classified": len(modules) + len(fe_files) - len(unclassified),
            "unclassified": len(unclassified),
            "unclassified_py": sum(1 for u in unclassified if u["lang"] == "py"),
            "unclassified_ts": sum(1 for u in unclassified if u["lang"] == "ts"),
            "coverage_pct": round(
                100 * (len(modules) + len(fe_files) - len(unclassified))
                / max(1, len(modules) + len(fe_files)), 1),
            "boxes": len(manifest["boxes"]),
            "phantom_boxes": len(phantom),
            "invariants": len(manifest["invariants"]),
            "violated": len(violations),
            "unchecked": len(unchecked),
            "processes": len(processes),
            "process_evidence_errors": process_evidence_errors,
            "authored_evidence_errors": authored_evidence_errors,
            "runtime_nodes": len(runtime_nodes),
            "memory_engine_zones": len(memory_engine["zones"]),
            "bounded_contexts": len(bounded_contexts),
            "decisions": len(decisions),
            "deployments": len(deployments),
            "fitness_regressions": fitness_regressions,
            "parse_errors": (arch.get("totals") or {}).get("parse_errors", 0),
        },
        "freshness": freshness,
        "manifest_problems": problems,
        "boxes": box_summary,
        "phantom_boxes": phantom,
        "unclassified": unclassified,
        "violations": violations,
        "unchecked_invariants": unchecked,
        "runtime_nodes": runtime_nodes,
        "runtime_connections": manifest["runtime_connections"],
        "memory_engine": memory_engine,
        "system_context": system_context,
        "bounded_contexts": bounded_contexts,
        "deployments": deployments,
        "decisions": decisions,
        "processes": processes,
        # Compatibility alias for the preliminary UI/API.
        "paths": processes,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--arch", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on stale artifacts, schema/evidence gaps, or fitness regressions",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    arch_path = Path(args.arch) if args.arch else root / "architecture" / "architecture.json"
    man_path = Path(args.manifest) if args.manifest else root / "architecture" / "manifest.yaml"
    out = Path(args.out) if args.out else root / "architecture" / "conformance.json"

    if not arch_path.exists():
        sys.stderr.write(f"missing {arch_path}; run extract_architecture.py first\n")
        return 2

    report = run(arch_path, man_path, root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    t = report["totals"]
    print(f"wrote {out.relative_to(root)}\n")
    print(f"  coverage        {t['classified']}/{t['units']} units ({t['coverage_pct']}%)"
          f"  [{t['modules']} py + {t['frontend_files']} ts]")
    print(f"  UNCLASSIFIED    {t['unclassified']}"
          f"  ({t['unclassified_py']} py, {t['unclassified_ts']} ts)")
    print(f"  PHANTOM boxes   {t['phantom_boxes']}  {report['phantom_boxes'] or ''}")
    print(f"  VIOLATION       {t['violated']} of {t['invariants']} invariants")
    print(f"  unchecked       {t['unchecked']} (stated in prose, no machine form)")
    print(f"  PROCESSES       {t['processes']}  "
          f"({t['process_evidence_errors']} evidence error(s))")
    print(f"  DOMAIN/ADR      {t['bounded_contexts']} contexts, "
          f"{t['decisions']} decisions, {t['deployments']} deployments")
    print(f"  FRESHNESS       {'current' if report['freshness']['fresh'] else 'STALE'}  "
          f"({report['freshness']['current_files']} source files)")
    print(f"  REGRESSIONS     {t['fitness_regressions']} beyond accepted drift baselines")

    if report["manifest_problems"]:
        print("\n  manifest problems:")
        for p in report["manifest_problems"]:
            print(f"    - {p}")

    if report["unclassified"]:
        print("\n  largest unclassified modules:")
        for u in report["unclassified"][:15]:
            print(f"    {u['loc']:>6}  {u['file']}")

    if report["violations"]:
        print("\n  violations:")
        for v in report["violations"]:
            print(f"    [{v['check']}] {v['id']}: {v['count']} hit(s)")
            print(f"        {v['statement']}")
            for h in v["hits"][:5]:
                loc = f":{h['line']}" if "line" in h else ""
                detail = h.get("imports") or h.get("reaches") or h.get("model") or h.get("kind") or ""
                print(f"        - {h.get('file', h.get('cycle'))}{loc}  {detail}")
            if v["truncated"]:
                print(f"        ... +{v['truncated']} more")

    for p in report["processes"]:
        if p["evidence_errors"]:
            print(f"\n  PROCESS_EVIDENCE {p['id']}: {p['evidence_errors']} error(s)")
            for step in p["steps"]:
                for evidence in step["evidence_results"]:
                    if not evidence["ok"]:
                        print(f"        - step {step['index']}: {evidence}")

    strict_failures = []
    if report["manifest_problems"]:
        strict_failures.append("manifest problems")
    if t["unclassified"]:
        strict_failures.append("unclassified source units")
    if t["phantom_boxes"]:
        strict_failures.append("phantom components")
    if t["process_evidence_errors"]:
        strict_failures.append("process evidence errors")
    if t["authored_evidence_errors"]:
        strict_failures.append("authored architecture evidence errors")
    if any(not node["evidence_ok"] for node in report["runtime_nodes"]):
        strict_failures.append("runtime evidence errors")
    if not report["freshness"]["fresh"]:
        strict_failures.append("stale architecture extraction")
    if t["fitness_regressions"]:
        strict_failures.append("fitness regressions beyond accepted baselines")
    if report["totals"].get("parse_errors"):
        strict_failures.append("source parse errors")

    if args.strict and strict_failures:
        print("\n  STRICT FAILURE: " + "; ".join(strict_failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
