"""Sole-writer guard for `delivery_pathways` (mind-delivery-plasticity).

Clone of the graph_writers scan, aimed at one table. The pathway ledger is
derived signal, but unlike passive derived columns it ACTUATES — it changes
what future sessions see — so it gets a single writer: the plasticity fold,
where the decision rule, the exploration floor, the tier-2 countersign
guard, and the audit logging live. A second writer would be a
delivery-policy mutation that skips all four.

Register: architecture/pathway_writers.json.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REGISTER = BACKEND.parent / "architecture" / "pathway_writers.json"

TABLE = "delivery_pathways"
ORM_CLASS = "DeliveryPathway"

_OP = re.compile(rf"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+({TABLE})\b", re.I)


def _register() -> dict:
    return json.loads(REGISTER.read_text())


def _allowed() -> set[str]:
    return {e["module"] for e in _register()["write"]}


def _modules() -> list[pathlib.Path]:
    return sorted((BACKEND / "app").rglob("*.py"))


def _rel(p: pathlib.Path) -> str:
    return p.relative_to(BACKEND).as_posix()


def _writes_in(tree: ast.AST) -> list[tuple[int, str]]:
    """Every write against the pathway table in one module's AST.

    ORM: a CALL of DeliveryPathway(...) constructs a row. A bare Name
    reference (select(DeliveryPathway), imports, annotations) is a read.
    SQL: string CONSTANTS containing INSERT/UPDATE/DELETE against the
    table — constants, not file text, so prose in docstrings that merely
    mentions the SQL cannot trip the scan (same lesson as graph_writers).
    """
    hits: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == ORM_CLASS):
            hits.append((n.lineno, f"{ORM_CLASS}(...)"))
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            for m in _OP.finditer(n.value):
                op = re.sub(r"\s+", " ", m.group(1)).upper()
                hits.append((n.lineno, f"{op} {TABLE}"))
    return hits


def scan() -> dict[str, list]:
    out: dict[str, list] = {}
    for p in _modules():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - app/ must always parse
            continue
        hits = _writes_in(tree)
        if hits:
            out[_rel(p)] = hits
    return out


@pytest.mark.hermetic
def test_only_the_sole_writer_touches_delivery_pathways():
    found = set(scan())
    rogue = sorted(found - _allowed())
    assert not rogue, (
        "ungoverned delivery_pathways writer in:\n"
        + "\n".join(f"  - {m}: {scan()[m]}" for m in rogue)
        + "\n\nPathway state changes only inside delivery_plasticity.py, where "
          "the decision rule, exploration floor, countersign guard, and audit "
          "logging live. Route the mutation through there."
    )


@pytest.mark.hermetic
def test_register_has_no_stale_entries():
    """A module that stopped writing must leave the register — a stale
    entry silently reserves a slot a future writer could inherit."""
    stale = sorted(_allowed() - set(scan()))
    assert not stale, f"pathway_writers.json lists non-writers: {stale}"


@pytest.mark.hermetic
def test_the_sole_writer_is_exactly_one_module():
    assert _allowed() == {"app/services/delivery_plasticity.py"}, (
        "the pathway ledger is single-writer BY DESIGN, not a debt register "
        "that may grow — adding a second module here needs a design change "
        "to mind-delivery-plasticity, not a register edit"
    )


# ── Honeypots: the scan must bite, and must not cry wolf ───────────────────

@pytest.mark.hermetic
def test_honeypot_a_planted_ungoverned_writer_is_caught():
    planted = ast.parse(
        "from app.models import DeliveryPathway\n"
        "def sneak(db):\n"
        "    db.add(DeliveryPathway(neuron_id=1, trigger='SessionStart'))\n"
    )
    assert _writes_in(planted), "ORM construction detector missed a planted writer"


@pytest.mark.hermetic
def test_honeypot_a_planted_raw_sql_update_is_caught():
    planted = ast.parse(
        'SQL = "UPDATE delivery_pathways SET state = \'retired\' WHERE id = :i"\n'
    )
    hits = _writes_in(planted)
    assert hits and "UPDATE" in hits[0][1], "raw SQL detector missed a planted UPDATE"


@pytest.mark.hermetic
def test_honeypot_a_read_is_not_a_write():
    innocent = ast.parse(
        "from sqlalchemy import select\n"
        "from app.models import DeliveryPathway\n"
        "async def peek(db):\n"
        "    return (await db.execute(select(DeliveryPathway))).scalars().all()\n"
    )
    assert not _writes_in(innocent), "a select() reference was misread as a write"


@pytest.mark.hermetic
def test_the_apply_dispatcher_never_learned_to_retire_pathways():
    """Countersign approval is a RECEIPT, not an actuator. The proposal-apply
    dispatcher must have no branch for 'pathway-retire' items: the retirement
    itself happens in the sole writer on the next plasticity pass, where the
    tier-2 assert lives. If you are adding such a branch, reconcile it with
    pathway_writers.json first — routing the mutation through apply would be
    a second writer wearing a countersign."""
    src = (BACKEND / "app/services/proposal_apply_service.py").read_text(
        encoding="utf-8")
    assert "pathway-retire" not in src, (
        "proposal_apply_service now dispatches pathway-retire items; the "
        "sole-writer boundary in pathway_writers.json must be redesigned "
        "before this ships"
    )
