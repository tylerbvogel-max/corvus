#!/usr/bin/env python3
"""Derive the frontend half of the architecture, and join it to the backend.

The Python extractor stops at the router. This walks frontend/src and answers the
question that actually spans the system: which component calls which endpoint.
That join is what makes a Path view mean anything end to end, and it is the only
reliable way to tell a live surface from a dead one — grepping strings gives
false positives, as it did on 2026-07-29.

Two call conventions exist in this codebase and both are handled:

  1. Typed wrappers. api.ts exports functions holding URL literals; components
     import the function by name. Resolved in two hops:
     component -> api.ts::listSessions -> /chat/sessions
  2. Direct fetch() inside a component, with the URL literal inline.

This is regex-and-brace parsing, not a real TypeScript parser. It is deliberately
biased toward false negatives: a URL it cannot resolve statically is reported as
unresolved rather than guessed at. Treat "no caller found" as a lead, not a
verdict.

The join matches against each route's `full_path` — the APIRouter prefix plus the
decorator path. 28 of 31 routers declare a prefix, so matching on the decorator
path alone silently fails for almost everything; an earlier version of this
script did exactly that and reported 16 of 200 routes as reachable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC_EXTS = {".ts", ".tsx"}
EXCLUDE = {"node_modules", "dist", "build", "__pycache__", ".git"}

# A URL literal: plain or template string starting with a slash. Rejects things
# like '/' alone and CSS-ish or regex-ish strings by requiring a word start.
URL_RE = re.compile(r"""['"`](/[a-zA-Z][\w\-/${}.:]*)['"`?]""")
IMPORT_RE = re.compile(
    r"""import\s+(?:type\s+)?(?:(\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?['"]([^'"]+)['"]""",
    re.S,
)
EXPORT_FN_RE = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)\s*\(")
FETCH_RE = re.compile(r"\bfetch\s*\(")


def normalize(url: str) -> str:
    """Collapse a URL to a shape comparable across both sides.

    `/chat/sessions/${id}`      -> /chat/sessions/{}
    `/chat/sessions?limit=${n}` -> /chat/sessions
    `/chat/sessions/{sid}`      -> /chat/sessions/{}   (backend decorator form)
    """
    url = url.split("?", 1)[0]
    url = re.sub(r"\$\{[^}]*\}", "{}", url)
    url = re.sub(r"\{[^}]*\}", "{}", url)
    url = re.sub(r"/+", "/", url)
    return url.rstrip("/") or "/"


def iter_sources(src: Path):
    for p in sorted(src.rglob("*")):
        if p.suffix in SRC_EXTS and not any(part in EXCLUDE for part in p.parts):
            yield p


def function_bodies(text: str) -> dict[str, str]:
    """Slice out each exported function body by brace matching. Good enough for
    api.ts-style modules; nested braces in strings can fool it, which is why
    results are only used to attribute URLs, never to drive control flow."""
    out: dict[str, str] = {}
    for m in EXPORT_FN_RE.finditer(text):
        name = m.group(1)
        i = text.find("{", m.end())
        if i == -1:
            continue
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out[name] = text[i:j]
    return out


def resolve_import(spec: str, from_file: Path, src: Path) -> str | None:
    if not spec.startswith("."):
        return None
    base = (from_file.parent / spec).resolve()
    for cand in (
        base.with_suffix(".ts"), base.with_suffix(".tsx"),
        base / "index.ts", base / "index.tsx", base,
    ):
        if cand.is_file():
            try:
                return str(cand.relative_to(src.parent.parent))
            except ValueError:
                return str(cand)
    return None


def build(root: Path) -> dict:
    src = root / "frontend" / "src"
    if not src.is_dir():
        return {"available": False, "reason": f"no such directory: {src}"}

    files: dict[str, dict] = {}
    for path in iter_sources(src):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root))

        imports, named = [], {}
        for m in IMPORT_RE.finditer(text):
            clause, spec = m.group(1), m.group(2)
            target = resolve_import(spec, path, src)
            if not target:
                continue
            imports.append(target)
            if clause and clause.startswith("{"):
                for raw in clause.strip("{}").split(","):
                    nm = raw.split(" as ")[-1].strip()
                    if nm:
                        named[nm] = target

        bodies = function_bodies(text)
        fn_urls = {
            name: sorted({normalize(u) for u in URL_RE.findall(body)})
            for name, body in bodies.items()
        }
        fn_urls = {k: v for k, v in fn_urls.items() if v}

        files[rel] = {
            "path": rel,
            "loc": text.count("\n") + 1,
            "imports": sorted(set(imports)),
            "named_imports": named,
            "urls_direct": sorted({normalize(u) for u in URL_RE.findall(text)}),
            "exports_with_urls": fn_urls,
            "uses_fetch": bool(FETCH_RE.search(text)),
        }

    # Second hop: a component that imports an api.ts function inherits its URLs.
    for rel, f in files.items():
        inherited: set[str] = set()
        for name, target in f["named_imports"].items():
            tgt = files.get(target)
            if tgt and name in tgt["exports_with_urls"]:
                inherited.update(tgt["exports_with_urls"][name])
        f["urls_via_imports"] = sorted(inherited)
        f["urls_all"] = sorted(set(f["urls_direct"]) | inherited)

    return {
        "available": True,
        "totals": {
            "files": len(files),
            "loc": sum(f["loc"] for f in files.values()),
            "files_calling_api": sum(1 for f in files.values() if f["urls_all"]),
            "files_using_fetch": sum(1 for f in files.values() if f["uses_fetch"]),
            "distinct_urls": len({u for f in files.values() for u in f["urls_all"]}),
        },
        "files": files,
    }


def join_routes(frontend: dict, backend_routes: list[dict]) -> dict:
    """Match frontend URLs to backend routes. Both sides normalized identically."""
    if not frontend.get("available"):
        return {"available": False}

    by_norm: dict[str, list[dict]] = {}
    for r in backend_routes:
        target = r.get("full_path") or r.get("path")
        if not target:
            continue
        by_norm.setdefault(normalize(target), []).append(r)

    def match(u: str) -> str | None:
        if u in by_norm:
            return u
        # Query strings built by interpolation without a literal '?' survive
        # normalization as a trailing '{}' — `/admin/alerts${qs}` becomes
        # '/admin/alerts{}'. Retry against the bare path before giving up.
        if u.endswith("{}"):
            bare = u[:-2].rstrip("/")
            if bare in by_norm:
                return bare
        return None

    callers: dict[str, set[str]] = {}
    unresolved: set[str] = set()
    for rel, f in frontend["files"].items():
        for u in f["urls_all"]:
            hit = match(u)
            if hit:
                callers.setdefault(hit, set()).add(rel)
            else:
                unresolved.add(u)

    uncalled = sorted(set(by_norm) - set(callers))
    return {
        "available": True,
        "totals": {
            "backend_routes": len(by_norm),
            "routes_called_by_frontend": len(callers),
            "routes_with_no_frontend_caller": len(uncalled),
            "frontend_urls_unmatched": len(unresolved),
        },
        "route_callers": {k: sorted(v) for k, v in sorted(callers.items())},
        "routes_with_no_frontend_caller": uncalled,
        "frontend_urls_unmatched": sorted(unresolved),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]

    fe = build(root)
    if not fe.get("available"):
        print(fe["reason"])
        return 1
    t = fe["totals"]
    print(f"  files              {t['files']}")
    print(f"  lines              {t['loc']:,}")
    print(f"  files calling API  {t['files_calling_api']}")
    print(f"  distinct URLs      {t['distinct_urls']}")

    arch_path = root / "architecture" / "architecture.json"
    if arch_path.exists():
        arch = json.loads(arch_path.read_text(encoding="utf-8"))
        j = join_routes(fe, arch["routes"])
        jt = j["totals"]
        print(f"\n  backend routes                 {jt['backend_routes']}")
        print(f"  called by frontend             {jt['routes_called_by_frontend']}")
        print(f"  NO frontend caller             {jt['routes_with_no_frontend_caller']}")
        print(f"  frontend URLs unmatched        {jt['frontend_urls_unmatched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
