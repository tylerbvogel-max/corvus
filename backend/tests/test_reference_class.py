"""Reference memory class (mind-reference-class) — hermetic tests.

Covers: the identity-wall predicate and its SQL rendering (all three
axes present in every identity-feeding query), extraction parsing and
chunking, authority clamping in the fact spec path, and the injection
hook's reference badge. Behavioral planted-neuron proofs (cannot-promote,
revocation round-trip) run live against a throwaway tenant — see the
mind-reference-class verification evidence.
"""

import importlib.util
import os
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import Neuron
from app.services.reference_class import (
    DOCUMENT_NODE_TYPE, PROMOTED_SOURCE_ORIGIN, REFERENCE_AUTHORITY_CAP,
    REFERENCE_NODE_TYPE, REFERENCE_REGION, REFERENCE_SOURCE_ORIGIN,
    is_reference, reference_exclusion_filters,
)
from app.services.reference_ingest import _chunk_document, _parse_facts


def _neuron(**kw) -> SimpleNamespace:
    base = dict(source_origin="distiller", node_type="lesson",
                department="Projects")
    base.update(kw)
    return SimpleNamespace(**base)


# ── is_reference: any axis marks the class ──────────────────────────

def test_is_reference_by_source_origin():
    assert is_reference(_neuron(source_origin=REFERENCE_SOURCE_ORIGIN))


def test_is_reference_by_node_type():
    assert is_reference(_neuron(node_type=REFERENCE_NODE_TYPE))
    assert is_reference(_neuron(node_type=DOCUMENT_NODE_TYPE))


def test_is_reference_by_region():
    assert is_reference(_neuron(department=REFERENCE_REGION))


def test_lesson_is_not_reference():
    assert not is_reference(_neuron())
    # A human-countersigned graduate has LEFT the reference class.
    assert not is_reference(_neuron(source_origin=PROMOTED_SOURCE_ORIGIN))


# ── The wall's SQL: all three axes must render ───────────────────────

def _compiled_wall_sql() -> str:
    stmt = select(Neuron.id).where(*reference_exclusion_filters())
    return str(stmt.compile(dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": True}))


def test_wall_sql_contains_all_three_axes():
    sql = _compiled_wall_sql()
    assert "source_origin" in sql and REFERENCE_SOURCE_ORIGIN in sql
    assert "node_type" in sql and REFERENCE_NODE_TYPE in sql
    assert "department" in sql and REFERENCE_REGION in sql


def test_wall_sql_keeps_null_departments():
    # Lessons with NULL scope must NOT be excluded by the region axis.
    assert "department IS NULL" in _compiled_wall_sql()


def test_wall_is_wired_into_identity_queries():
    """Charter promotion, cluster load, and self-model growth must all
    call reference_exclusion_filters — grep-level proof that the wall
    cannot be bypassed by editing one call site.

    The charter's own render reaches the wall through
    delivery_mode.charter_eligible_filters (mind-charter-composition
    centralised the charter eligibility gate there so the delivery
    classifier judges exactly the population the charter draws from), so
    that indirection is asserted rather than counted.

    The two janitor-side call sites now live in two files: record 04b moved
    the cluster loader (_load_lessons) down into mind_corpus to break the
    maintenance import cycle, and the wall travelled with it. mind_janitors
    keeps the charter-promotion site. The AGGREGATE bar is unchanged at two —
    splitting the count per file, rather than lowering it, is what stops this
    guard from being quietly satisfied by a future move."""
    import app.services.delivery_mode as dm
    import app.services.mind_corpus as mc
    import app.services.mind_janitors as mj
    import app.services.skill_compiler as sc

    def _count(module) -> int:
        with open(module.__file__, encoding="utf-8") as fh:
            return fh.read().count("reference_exclusion_filters()")

    for module, count in ((mc, 1), (mj, 1), (sc, 1), (dm, 1)):
        assert _count(module) >= count, \
            f"{module.__name__} lost a reference wall call site"
    assert _count(mc) + _count(mj) >= 2, \
        "cluster load and charter promotion must BOTH still carry the wall"
    with open(sc.__file__, encoding="utf-8") as fh:
        assert "charter_eligible_filters()" in fh.read(), \
            "charter render must reach the wall via the shared gate"


def test_charter_eligibility_gate_carries_the_wall():
    """Runtime proof, not grep: the charter's actual selection clause
    excludes reference-class neurons on every axis it always has."""
    from sqlalchemy import select

    from app.models import Neuron
    from app.services.delivery_mode import charter_eligible_filters

    clause = str(select(Neuron.id).where(*charter_eligible_filters()))
    wall = _compiled_wall_sql()
    assert "neurons.source_origin != " in clause
    assert "department IS NULL" in wall and "department IS NULL" in clause


def test_authority_cap_is_informational():
    assert REFERENCE_AUTHORITY_CAP == "informational"
    from app.services.write_gate import authority_rank
    assert authority_rank(REFERENCE_AUTHORITY_CAP) == min(
        authority_rank("guidance"), authority_rank("organizational"),
        authority_rank(REFERENCE_AUTHORITY_CAP))


# ── Extraction parsing ───────────────────────────────────────────────

def test_parse_facts_happy_path():
    text = ('noise before [{"label": "Takt time", "content": "Takt = time/demand",'
            '"summary": "s", "abstraction_type": "concept",'
            '"entities": ["Takt Time"], "section_ref": "2.1"}] noise after')
    facts = _parse_facts(text)
    assert len(facts) == 1
    assert facts[0]["label"] == "Takt time"
    assert facts[0]["section_ref"] == "2.1"


def test_parse_facts_rejects_garbage_and_clamps():
    assert _parse_facts("no json here") == []
    assert _parse_facts('{"not": "an array"}') == []
    facts = _parse_facts(
        '[{"label": "' + "x" * 400 + '", "content": "c",'
        '"abstraction_type": "made-up", "entities": "not-a-list"}]')
    assert len(facts) == 1
    assert len(facts[0]["label"]) == 150
    assert facts[0]["abstraction_type"] == "concept"  # invalid → default
    assert facts[0]["entities"] == []


def test_parse_facts_drops_empty_facts():
    facts = _parse_facts('[{"label": "", "content": "c"}, {"label": "ok",'
                         '"content": ""}, "not a dict", 42]')
    assert facts == []


# ── Chunking ─────────────────────────────────────────────────────────

def test_small_doc_is_single_shot():
    structure = SimpleNamespace(sections=[])
    chunks = _chunk_document("hello " * 100, structure)
    assert len(chunks) == 1
    assert chunks[0][0] == "whole document"


def test_large_doc_chunks_cover_everything():
    text = "x" * 150_000
    sections = [SimpleNamespace(char_start=i) for i in range(0, 150_000, 10_000)]
    chunks = _chunk_document(text, SimpleNamespace(sections=sections))
    assert len(chunks) > 1
    assert sum(len(c[1]) for c in chunks) == len(text)


# ── Injection hook badge ─────────────────────────────────────────────

def _load_hook():
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "harness", "claude-code", "memory_inject_hook.py")
    spec = importlib.util.spec_from_file_location("memory_inject_hook",
                                                  os.path.abspath(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_badges_reference_hits():
    hook = _load_hook()
    text = hook._format_context([
        {"label": "Takt time", "summary": "time/demand", "scope": "Library",
         "reference": True, "source": "IE-Handbook", "as_of": "2026-07-14"},
        {"label": "port check", "summary": "use fuser", "scope": "Environment"},
    ])
    assert "[reference: IE-Handbook]" in text
    assert "[Environment]" in text
    assert "not lived experience" in text
    assert "reference" in hook.INJECTABLE_TYPES


# ── Scoring-health segmentation ──────────────────────────────────────

def _score_rows():
    import datetime as dt
    ts = dt.datetime(2026, 7, 14)
    import json as j
    return [
        (1, j.dumps([{"neuron_id": 10, "novelty": 0.0, "relevance": 0.9},
                     {"neuron_id": 99, "novelty": 0.8, "relevance": 0.7}]), ts),
        (2, j.dumps([{"neuron_id": 99, "novelty": 0.75, "relevance": 0.6}]), ts),
    ]


def test_scoring_health_segments_by_origination():
    from app.routers.admin import _parse_query_scores
    ref_ids = {99}
    exp = _parse_query_scores(_score_rows(), ["novelty"], ref_ids)
    lib = _parse_query_scores(_score_rows(), ["novelty"], ref_ids, exclude=False)
    # experiential keeps only neuron 10 (query 2 drops out entirely —
    # its whole population was reference)
    assert [q["signals"]["novelty"] for q in exp] == [[0.0]]
    assert [q["signals"]["novelty"] for q in lib] == [[0.8], [0.75]]


def test_scoring_health_unfiltered_is_legacy():
    from app.routers.admin import _parse_query_scores
    all_scores = _parse_query_scores(_score_rows(), ["novelty"])
    assert [q["signals"]["novelty"] for q in all_scores] == [[0.0, 0.8], [0.75]]


def test_hook_no_reference_note_without_reference_hits():
    hook = _load_hook()
    text = hook._format_context(
        [{"label": "l", "summary": "s", "scope": "User"}])
    assert "reference" not in text.split("\n")[1]
    assert "not lived experience" not in text
