"""The sole-writer guard: who may create, delete, or content-mutate the graph.

Record 04 owes "a source/behavior test proves no new ungoverned writer was
introduced". That invariant was enforced for exactly ONE file before this —
``test_concept_governance.py`` parses ``concept_service.py`` and asserts it
constructs no ``Neuron``/``NeuronEdge`` — leaving every other module in the
codebase on the honour system.

Three rules, in ascending order of how much they protect:

1. CREATE is allowlisted. Only modules named in
   ``architecture/graph_writers.json`` may construct a ``Neuron``/``NeuronEdge``
   or ``INSERT INTO`` those tables.
2. DELETE is allowlisted, separately, because destructive access is rarer and
   should stay that way.
3. RAW SQL MAY NEVER ASSIGN AN AUTHORED-CONTENT OR LIFECYCLE COLUMN. This one is
   not a list, it is absolute, and it is the reason this file exists. Content,
   authority, scope and the supersession lifecycle (``is_active``,
   ``superseded_by``) change only through the ORM and the action bus — which is
   where the evidence gates and the countersign path live. Measured true across
   the whole app on 2026-08-01, and enforced by nothing until now. Derived
   signal (``co_fire_count``, ``weight``, ``invocations``, ``avg_utility``,
   ``centrality``, ``weak_edges``, ``edge_type``) is deliberately NOT protected:
   it is computed from firings and graph shape, not authored.

The allowlist is a DEBT REGISTER, not a blessing — see the comment in the JSON.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REGISTER = BACKEND.parent / "architecture" / "graph_writers.json"

GRAPH_TABLES = ("neurons", "neuron_edges")
ORM_CLASSES = {"Neuron", "NeuronEdge"}

_OP = re.compile(
    rf"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+({'|'.join(GRAPH_TABLES)})\b", re.I
)


def _register() -> dict:
    return json.loads(REGISTER.read_text())


def _modules() -> list[pathlib.Path]:
    return sorted((BACKEND / "app").rglob("*.py"))


def _rel(p: pathlib.Path) -> str:
    return p.relative_to(BACKEND).as_posix()


def _sql_literals(tree: ast.AST):
    """Every string constant that contains a write against a graph table.

    Reading string CONSTANTS rather than the file text matters: an earlier
    version of this scan grepped whole sources and flagged prose in comments and
    docstrings that merely mentioned the SQL.
    """
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _OP.search(n.value):
            yield n


def _set_assignments(sql: str) -> list[str]:
    """Column names ASSIGNED by an UPDATE ... SET clause.

    Splits the SET clause on top-level commas and takes the left side of each
    assignment. Expression-internal comparisons must not count: the CASE in
    admin.py reads ``src.department = tgt.department`` to CHOOSE an edge_type,
    and a naive ``\\bcolumn\\s*=`` scan reports that as writing `department`.
    That false positive is pinned by a honeypot below.
    """
    flat = re.sub(r"\s+", " ", sql)
    m = re.search(r"\bSET\b(.*)", flat, re.I)
    if not m:
        return []
    clause = m.group(1)
    # Stop at the first top-level WHERE/FROM (depth 0 only).
    depth, end = 0, len(clause)
    for i, ch in enumerate(clause):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and re.match(r"\s(WHERE|FROM)\s", clause[i:i + 7], re.I):
            end = i
            break
    clause = clause[:end]

    parts, depth, buf = [], 0, ""
    for ch in clause:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)

    assigned = []
    for part in parts:
        lhs, sep, _ = part.partition("=")
        if not sep:
            continue
        name = lhs.strip().split(".")[-1].strip().strip('"')
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
            assigned.append(name.lower())
    return assigned


def scan() -> dict[str, dict[str, list]]:
    """Every graph write in app/, classified. The single source of truth here."""
    out: dict[str, dict[str, list]] = {}
    for p in _modules():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - app/ must always parse
            continue
        rec = {"create": [], "delete": [], "content_update": []}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in ORM_CLASSES):
                rec["create"].append((n.lineno, f"{n.func.id}(...)"))
        for node in _sql_literals(tree):
            for m in _OP.finditer(node.value):
                op = re.sub(r"\s+", " ", m.group(1)).upper()
                if op == "INSERT INTO":
                    rec["create"].append((node.lineno, f"INSERT INTO {m.group(2)}"))
                elif op == "DELETE FROM":
                    rec["delete"].append((node.lineno, f"DELETE FROM {m.group(2)}"))
        # Content updates are judged per literal, not per match.
        for node in _sql_literals(tree):
            protected = set(_register()["protected_columns"])
            hit = sorted(set(_set_assignments(node.value)) & protected)
            if hit:
                rec["content_update"].append((node.lineno, ",".join(hit)))
        if any(rec.values()):
            out[_rel(p)] = rec
    return out


def _allowed(kind: str) -> set[str]:
    return {e["module"] for e in _register()[kind]}


# ── The three rules ────────────────────────────────────────────────────────

@pytest.mark.hermetic
def test_only_allowlisted_modules_create_graph_rows():
    found = {m for m, r in scan().items() if r["create"]}
    rogue = sorted(found - _allowed("create"))
    assert not rogue, (
        "ungoverned neuron/edge CREATION in:\n"
        + "\n".join(f"  - {m}: {scan()[m]['create']}" for m in rogue)
        + "\n\nRoute the write through the action bus, or — if it genuinely "
          "belongs — add it to architecture/graph_writers.json with a reason. "
          "That file is a debt register; adding to it should feel like a cost."
    )


@pytest.mark.hermetic
def test_only_allowlisted_modules_delete_graph_rows():
    found = {m for m, r in scan().items() if r["delete"]}
    rogue = sorted(found - _allowed("delete"))
    assert not rogue, (
        "ungoverned neuron/edge DELETION in:\n"
        + "\n".join(f"  - {m}: {scan()[m]['delete']}" for m in rogue)
    )


@pytest.mark.hermetic
def test_no_raw_sql_assigns_authored_content_or_lifecycle():
    """The absolute rule. No allowlist, because there is no good reason.

    Authored content and the supersession lifecycle carry the evidence gates.
    A hand-written UPDATE that sets ``is_active`` or ``superseded_by`` would
    retire a memory without passing them, and would look like maintenance.
    """
    offenders = {m: r["content_update"] for m, r in scan().items() if r["content_update"]}
    assert not offenders, (
        "raw SQL assigns protected content/lifecycle columns:\n"
        + "\n".join(f"  - {m}:{ln} sets {cols}" for m, hits in offenders.items()
                    for ln, cols in hits)
        + "\n\nThese columns change through the ORM and the action bus only — "
          "that is where the evidence gates and countersign path live."
    )


# ── Ratchet direction 2 ────────────────────────────────────────────────────

@pytest.mark.hermetic
@pytest.mark.parametrize("kind", ["create", "delete"])
def test_register_has_no_stale_entries(kind):
    """A module that stopped writing must leave the register.

    Same reasoning as the cycle allowlist: a stale entry silently reserves a
    slot a future writer could inherit without review.
    """
    actual = {m for m, r in scan().items() if r[kind]}
    stale = sorted(_allowed(kind) - actual)
    assert not stale, (
        f"graph_writers.json lists {kind} modules that no longer write: {stale}. "
        f"Good news — delete the entries so the register keeps shrinking."
    )


# ── Honeypots: the scan must bite, and must not cry wolf ───────────────────

@pytest.mark.hermetic
def test_honeypot_a_planted_ungoverned_writer_is_caught(tmp_path):
    planted = ast.parse(
        "from app.models import Neuron\n"
        "def sneak(db):\n"
        "    db.add(Neuron(label='x'))\n"
    )
    calls = [n for n in ast.walk(planted)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in ORM_CLASSES]
    assert calls, "ORM construction detector missed a planted writer"


@pytest.mark.hermetic
def test_honeypot_a_planted_lifecycle_update_is_caught():
    sql = "UPDATE neurons SET is_active = false, superseded_by = :nid WHERE id = :id"
    assigned = set(_set_assignments(sql))
    protected = set(_register()["protected_columns"])
    assert {"is_active", "superseded_by"} <= assigned & protected, (
        "a hand-written supersession UPDATE was not detected"
    )


@pytest.mark.hermetic
def test_honeypot_a_comparison_inside_case_is_not_an_assignment():
    """Regression pin for a real false positive found building this guard.

    admin.py:2434 reads ``src.department = tgt.department`` inside a CASE to
    choose an edge_type. A naive scan called that a write to `department` and
    would have failed the absolute rule against a module doing nothing wrong.
    """
    sql = ("UPDATE neuron_edges SET edge_type = CASE WHEN src.department = "
           "tgt.department THEN 'stellate' ELSE 'pyramidal' END FROM x WHERE y")
    assigned = _set_assignments(sql)
    assert assigned == ["edge_type"], f"expected only edge_type, got {assigned}"
    assert "department" not in assigned


@pytest.mark.hermetic
def test_honeypot_sql_in_a_comment_is_not_a_write():
    """Prose that mentions the SQL must not trip the scan.

    The first version of this scan grepped file text and flagged docstrings.
    """
    tree = ast.parse('"""We used to DELETE FROM neurons here."""\nx = 1\n')
    # Docstrings ARE string constants, so the guard is the caller's use of
    # _sql_literals on real statements; assert the shape we depend on.
    lits = list(_sql_literals(tree))
    assert lits, "sanity: the docstring is a string constant"
    assert not _set_assignments(lits[0].value), "a comment produced an assignment"


@pytest.mark.hermetic
def test_derived_signal_columns_are_deliberately_unprotected():
    """Guard against over-tightening later: these are computed, not authored."""
    protected = set(_register()["protected_columns"])
    derived = {"co_fire_count", "weight", "invocations", "avg_utility",
               "centrality", "weak_edges", "edge_type", "last_adjusted"}
    overlap = protected & derived
    assert not overlap, (
        f"{sorted(overlap)} are derived signal, recomputed from firings and graph "
        f"shape. Protecting them would fail synaptic learning and edge tiering "
        f"for no integrity gain."
    )
