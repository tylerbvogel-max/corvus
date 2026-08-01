#!/usr/bin/env python3
"""Derive the Corvus architecture from source. Deterministic, stdlib-only, no LLM.

Walks every Python module in the repo and emits a single JSON artifact describing
what the code actually does: module inventory, the internal import graph, a
function-level call graph, the HTTP/MCP surface mapped through handlers to the
services they reach, and the call sites that touch the database or an LLM.

This script has no opinion about architecture. It does not know what a "layer"
is. Classification lives in architecture/manifest.yaml, authored by hand, and
the two are compared by check_architecture.py. Keeping derivation and opinion in
separate files is the whole point: the diff between them is the product.

Usage:
    python backend/scripts/extract_architecture.py
    python backend/scripts/extract_architecture.py --out architecture/architecture.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 2

EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".claude", "site-packages",
}

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Modules that mean "an LLM is being called". Membership is by import target or
# attribute base, not by string matching in source, so comments don't count.
LLM_MODULE_HINTS = {
    "app.services.llm_provider",
    "app.services.claude_cli",
    "anthropic",
    "openai",
}
LLM_CALL_HINTS = {
    "complete", "completion", "call_llm", "invoke_llm", "ask", "run_claude",
    "claude_call", "messages",
}

# SQLAlchemy-shaped mutation surface.
DB_WRITE_ATTRS = {"add", "add_all", "commit", "delete", "merge", "flush", "bulk_save_objects"}
DB_WRITE_FUNCS = {"insert", "update", "delete"}


# --------------------------------------------------------------------------
# module discovery + name resolution
# --------------------------------------------------------------------------

def iter_python_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def source_inventory(root: Path) -> list[Path]:
    """Return exactly the source units represented by this extractor.

    The fingerprint is intentionally scoped to extracted Python plus frontend
    TypeScript. Runtime files cited by the authored manifest are existence-
    checked separately; pretending this static extractor understands their
    semantics would make the freshness signal broader than its evidence.
    """
    paths = list(iter_python_files(root))
    frontend_src = root / "frontend" / "src"
    if frontend_src.is_dir():
        paths.extend(
            path for path in frontend_src.rglob("*")
            if path.is_file()
            and path.suffix in {".ts", ".tsx"}
            and not any(part in EXCLUDE_DIRS for part in path.parts)
        )
    return sorted(set(paths))


def source_fingerprint(root: Path) -> dict:
    """Content-address the extracted source inventory, including file names."""
    digest = hashlib.sha256()
    paths = source_inventory(root)
    for path in paths:
        rel = str(path.relative_to(root))
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "algorithm": "sha256",
        "fingerprint": digest.hexdigest(),
        "files": len(paths),
    }


def module_name_for(path: Path, root: Path) -> str:
    """Best-effort dotted module name.

    The backend is imported as `app.*` (its sys.path root is backend/), so
    backend/app/services/foo.py resolves to app.services.foo. Everything else
    is named by its repo-relative path so it still gets a stable key.
    """
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    if parts[:1] == ["backend"]:
        parts = parts[1:]
    return ".".join(parts)


def find_routers(tree: ast.Module) -> dict[str, str]:
    """Map module-level router variables to their mount prefix.

    Run as a pre-pass because a decorator may reference a router assigned
    anywhere in the module, and because the variable is not always named
    `router`. Missing this drops every route on the odd-named ones.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name != "APIRouter":
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = kw.value.value or ""
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = prefix
    return out


@dataclass
class ModuleInfo:
    path: str
    module: str
    loc: int
    imports: set[str] = field(default_factory=set)          # resolved internal modules
    external_imports: set[str] = field(default_factory=set)
    symbol_origin: dict[str, str] = field(default_factory=dict)  # local name -> module
    functions: dict[str, dict] = field(default_factory=dict)
    routes: list[dict] = field(default_factory=list)
    db_writes: list[dict] = field(default_factory=list)
    llm_sites: list[dict] = field(default_factory=list)
    defines: set[str] = field(default_factory=set)

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "module": self.module,
            "loc": self.loc,
            "imports": sorted(self.imports),
            "external_imports": sorted(self.external_imports),
            "functions": self.functions,
            "routes": self.routes,
            "db_writes": self.db_writes,
            "llm_sites": self.llm_sites,
        }


class ModuleVisitor(ast.NodeVisitor):
    """Single-pass collector. Tracks the enclosing function so every finding
    (a route, a db write, an llm call) can be attributed to a definition rather
    than just a file — the Paths view needs function granularity."""

    def __init__(self, info: ModuleInfo, known_modules: set[str],
                 routers: dict[str, str] | None = None):
        self.info = info
        self.known = known_modules
        self.stack: list[str] = []
        # var name -> mount prefix, from a pre-pass over module-level assignments.
        # 28 of 31 routers here declare a prefix, and a module may define more
        # than one router (proposals.py has `router` and `provenance_router`),
        # so neither the prefix nor the variable name can be assumed.
        self.routers = routers or {}
        # local variable name -> model class it was constructed from
        self.model_vars: dict[str, str] = {}

    # -- imports ----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = self._resolve(alias.name)
            local = alias.asname or alias.name.split(".")[0]
            if target:
                self.info.imports.add(target)
                self.info.symbol_origin[local] = target
            else:
                self.info.external_imports.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._resolve_from(node)
        if base is None:
            if node.module:
                self.info.external_imports.add(node.module.split(".")[0])
            self.generic_visit(node)
            return
        for alias in node.names:
            # `from app.services import foo` -> the submodule, not a symbol
            submodule = f"{base}.{alias.name}"
            target = submodule if submodule in self.known else base
            self.info.imports.add(target)
            self.info.symbol_origin[alias.asname or alias.name] = target
        self.generic_visit(node)

    def _resolve(self, dotted: str) -> str | None:
        if dotted in self.known:
            return dotted
        # walk up: app.services.foo.bar -> app.services.foo
        parts = dotted.split(".")
        while len(parts) > 1:
            parts.pop()
            cand = ".".join(parts)
            if cand in self.known:
                return cand
        return None

    def _resolve_from(self, node: ast.ImportFrom) -> str | None:
        if node.level:  # relative import
            own = self.info.module.split(".")
            base = own[: len(own) - node.level + 1] if node.level <= len(own) else []
            if node.module:
                base = base + node.module.split(".")
            cand = ".".join(base)
            return cand if cand in self.known else self._resolve(cand)
        if not node.module:
            return None
        return self._resolve(node.module)

    # -- definitions ------------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, is_async=True)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track `x = SomeModel(...)` so a later db.add(x) can be attributed.

        Without this, sole_writer silently misses every write that goes through
        a local variable — which is most of them — and reports a clean run it
        has no basis for. A blind check that reads green is worse than no check.
        """
        if isinstance(node.value, ast.Call):
            fn = node.value.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name and name[:1].isupper():
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        self.model_vars[tgt.id] = name
        self.generic_visit(node)

    def _function(self, node, is_async: bool) -> None:
        qual = ".".join(self.stack + [node.name])
        self.info.defines.add(qual)
        routes = self._routes_from_decorators(node)
        self.info.functions[qual] = {
            "name": qual,
            "line": node.lineno,
            "is_async": is_async,
            "calls": [],
            "is_route": bool(routes),
        }
        for r in routes:
            r["handler"] = qual
            r["line"] = node.lineno
            self.info.routes.append(r)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.info.defines.add(".".join(self.stack + [node.name]))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _routes_from_decorators(self, node) -> list[dict]:
        found = []
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            if not isinstance(fn, ast.Attribute) or fn.attr not in HTTP_VERBS:
                continue
            base = fn.value
            base_name = base.id if isinstance(base, ast.Name) else None
            if base_name is None:
                continue
            if base_name != "app" and base_name not in self.routers:
                continue
            path = None
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = dec.args[0].value
            prefix = self.routers.get(base_name, "")
            full = f"{prefix}{path}" if path else prefix
            full = re.sub(r"/+", "/", full) if full else full
            found.append({
                "method": fn.attr.upper(),
                "path": path,               # as written on the decorator
                "prefix": prefix,           # from APIRouter(prefix=...)
                "full_path": full or None,  # what a client actually calls
                "mounted_on": base_name,
            })
        return found

    # -- call sites -------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        enclosing = ".".join(self.stack) if self.stack else "<module>"
        fn = node.func

        if isinstance(fn, ast.Attribute):
            if fn.attr in DB_WRITE_ATTRS:
                self.info.db_writes.append({
                    "kind": fn.attr, "line": node.lineno, "in": enclosing,
                    "target": self._describe(fn.value),
                    "model": self._model_arg(node),
                })
            if fn.attr in LLM_CALL_HINTS and self._origin_of(fn.value) in LLM_MODULE_HINTS:
                self.info.llm_sites.append({
                    "kind": fn.attr, "line": node.lineno, "in": enclosing,
                })
        elif isinstance(fn, ast.Name):
            if fn.id in DB_WRITE_FUNCS and fn.id in self.info.symbol_origin:
                self.info.db_writes.append({
                    "kind": fn.id, "line": node.lineno, "in": enclosing, "target": "sqlalchemy",
                    "model": self._model_arg(node),
                })
            origin = self.info.symbol_origin.get(fn.id)
            if origin in LLM_MODULE_HINTS or (origin and origin.startswith("app.services.llm")):
                self.info.llm_sites.append({
                    "kind": fn.id, "line": node.lineno, "in": enclosing,
                })

        target = self._call_target(fn)
        if target and enclosing in self.info.functions:
            calls = self.info.functions[enclosing]["calls"]
            if target not in calls:
                calls.append(target)

        self.generic_visit(node)

    def _origin_of(self, node) -> str | None:
        if isinstance(node, ast.Name):
            return self.info.symbol_origin.get(node.id)
        return None

    def _model_arg(self, node: ast.Call) -> str | None:
        """Name the ORM model a write targets, so `sole_writer` invariants are
        checkable. Covers db.add(Neuron(...)), update(Neuron), and
        db.query(Neuron) style first arguments; returns None when the target is
        a runtime value we cannot name statically."""
        if not node.args:
            return None
        first = node.args[0]
        if isinstance(first, ast.Call):
            first = first.func
        if isinstance(first, ast.Name):
            name = first.id
            if name[:1].isupper():
                return name
            return self.model_vars.get(name)
        if isinstance(first, ast.Attribute):
            return first.attr if first.attr[:1].isupper() else None
        return None

    def _describe(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self._describe(node.value)}.{node.attr}"
        return "<expr>"

    def _call_target(self, fn) -> str | None:
        """Resolve a call to `module::symbol` when the callee came from an import."""
        if isinstance(fn, ast.Name):
            origin = self.info.symbol_origin.get(fn.id)
            return f"{origin}::{fn.id}" if origin else None
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            origin = self.info.symbol_origin.get(fn.value.id)
            return f"{origin}::{fn.attr}" if origin else None
        return None


# --------------------------------------------------------------------------
# graph passes
# --------------------------------------------------------------------------

def reachable_modules(start: str, edges: dict[str, set[str]]) -> set[str]:
    seen, queue = set(), deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in edges.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def find_cycles(edges: dict[str, set[str]], limit: int = 50) -> list[list[str]]:
    """Tarjan SCCs; any component larger than one node is an import cycle."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    out: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(edges.get(v, ())):
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    sys.setrecursionlimit(10000)
    for node in sorted(edges):
        if node not in index:
            strongconnect(node)
    return sorted(out)[:limit]


def build(root: Path) -> dict:
    files = sorted(iter_python_files(root))
    names = {module_name_for(p, root): p for p in files}
    known = set(names)

    modules: dict[str, ModuleInfo] = {}
    parse_errors: list[dict] = []

    for mod, path in names.items():
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError as exc:
            parse_errors.append({"module": mod, "path": str(path.relative_to(root)), "error": str(exc)})
            continue
        info = ModuleInfo(
            path=str(path.relative_to(root)),
            module=mod,
            loc=src.count("\n") + 1,
        )
        ModuleVisitor(info, known, find_routers(tree)).visit(tree)
        info.imports.discard(mod)  # self-import from the walk-up resolver
        modules[mod] = info

    import_edges = {m: set(i.imports) for m, i in modules.items()}

    # Routes, with the module set each handler can reach. Import-reachability is
    # an over-approximation of what a request touches; call-reachability is the
    # tighter set. Both are emitted so the Paths view can show the difference
    # rather than quietly picking one.
    call_edges: dict[str, set[str]] = defaultdict(set)
    for mod, info in modules.items():
        for fn in info.functions.values():
            for target in fn["calls"]:
                tmod = target.split("::", 1)[0]
                if tmod in modules:
                    call_edges[mod].add(tmod)

    routes = []
    for mod, info in modules.items():
        for r in info.routes:
            entry = dict(r)
            entry["module"] = mod
            entry["file"] = info.path
            entry["imports_reachable"] = sorted(reachable_modules(mod, import_edges))
            entry["calls_reachable"] = sorted(reachable_modules(mod, call_edges))
            routes.append(entry)

    route_modules = {r["module"] for r in routes}
    request_reachable: set[str] = set()
    for m in route_modules:
        request_reachable.add(m)
        request_reachable |= reachable_modules(m, call_edges)

    # Anything with a top-level async entry function that no request can reach is
    # a background candidate. Derived, not guessed from filenames.
    background_candidates = []
    for mod, info in modules.items():
        if mod in request_reachable:
            continue
        entries = [
            f["name"] for f in info.functions.values()
            if f["is_async"] and "." not in f["name"]
        ]
        if entries:
            background_candidates.append({"module": mod, "entrypoints": sorted(entries)})

    imported_by: dict[str, list[str]] = defaultdict(list)
    for m, targets in import_edges.items():
        for t in targets:
            imported_by[t].append(m)

    # Frontend pass + the route join. Kept in a separate module because it is
    # regex parsing rather than AST, with different reliability characteristics;
    # merged here so there is one artifact and no ordering foot-gun.
    try:
        import extract_frontend
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import extract_frontend
    frontend = extract_frontend.build(root)
    route_join = extract_frontend.join_routes(frontend, routes)

    return {
        "schema_version": SCHEMA_VERSION,
        # This committed artifact must be byte-identical in every checkout.
        # Absolute roots made identical source look stale on CI and in clean
        # worktrees, defeating the conformance gate itself.
        "root": ".",
        "source_inventory": source_fingerprint(root),
        "frontend": frontend,
        "route_join": route_join,
        "totals": {
            "modules": len(modules),
            "loc": sum(i.loc for i in modules.values()),
            "routes": len(routes),
            "import_edges": sum(len(v) for v in import_edges.values()),
            "call_edges": sum(len(v) for v in call_edges.values()),
            "modules_touching_db": sum(1 for i in modules.values() if i.db_writes),
            "modules_touching_llm": sum(1 for i in modules.values() if i.llm_sites),
            "parse_errors": len(parse_errors),
        },
        "modules": {m: i.to_json() for m, i in sorted(modules.items())},
        "imported_by": {k: sorted(v) for k, v in sorted(imported_by.items())},
        "call_edges": {k: sorted(v) for k, v in sorted(call_edges.items())},
        "routes": sorted(routes, key=lambda r: (r["file"], r.get("path") or "")),
        "background_candidates": sorted(background_candidates, key=lambda b: b["module"]),
        "import_cycles": find_cycles(import_edges),
        "parse_errors": parse_errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="repo root (default: two levels up from this script)")
    ap.add_argument("--out", default=None, help="output path (default: <root>/architecture/architecture.json)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    out = Path(args.out).resolve() if args.out else root / "architecture" / "architecture.json"

    data = build(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    if not args.quiet:
        t = data["totals"]
        print(f"wrote {out.relative_to(root)}")
        print(f"  modules            {t['modules']}")
        print(f"  lines              {t['loc']}")
        print(f"  routes             {t['routes']}")
        print(f"  import edges       {t['import_edges']}")
        print(f"  call edges         {t['call_edges']}")
        print(f"  modules w/ db      {t['modules_touching_db']}")
        print(f"  modules w/ llm     {t['modules_touching_llm']}")
        print(f"  import cycles      {len(data['import_cycles'])}")
        print(f"  parse errors       {t['parse_errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
