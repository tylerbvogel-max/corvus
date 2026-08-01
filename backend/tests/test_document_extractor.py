"""Unit tests for the whole-document extraction path (document_extractor.py).

Hermetic — mocks `llm_chat` and uses a minimal fake async session. No Postgres,
no real Claude CLI. The integration check against MIL-STD-1587E is a manual
smoke test (see CORVUS-STATUS.md notes on the ingest pipeline).
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.models import AutopilotProposal, DocumentIngestJob, ProposalItem
from app.services.document_extractor import (
    WHOLE_DOC_THRESHOLD_CHARS,
    _build_whole_doc_prompt,
    _find_json_array_span,
    _group_label_and_page,
    _group_whole_doc_proposals,
    _inject_page_markers,
    _parse_llm_proposals,
    create_whole_doc_proposals,
    extract_whole_document,
)
from app.services.document_parser import DocumentStructure, Section


# ── Fake async session ──────────────────────────────────────────────────


class _FakeSession:
    """Minimal shape covering add/flush + sequential ID assignment."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self._next_id = 1_000

    def add(self, obj: Any) -> None:
        # Assign a stable id so .flush() → .id lookup works like SQLAlchemy.
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        self.added.append(obj)

    async def flush(self) -> None:
        # no-op; ids already assigned in add()
        pass


# ── Factories ───────────────────────────────────────────────────────────


def _make_job(**overrides: Any) -> DocumentIngestJob:
    job = DocumentIngestJob(
        id="test0000abcd",
        filename="TEST-STD-1.pdf",
        file_format="pdf",
        title="Test Standard 1",
        source_type="regulatory",
        authority_level="mandatory",
        citation="TEST-STD-1, 2026",
        source_url="",
        department="Engineering",
        role_key="materials_engineer",
        model="opus",
        status="extracting",
    )
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def _make_structure(sections: list[Section] | None = None) -> DocumentStructure:
    return DocumentStructure(
        title="Test Standard 1",
        total_pages=20,
        sections=sections or [],
    )


def _make_section(
    sid: str, title: str, char_start: int, page_start: int | None = 1,
) -> Section:
    return Section(
        id=sid,
        title=title,
        level=1,
        char_start=char_start,
        char_end=char_start + 100,
        page_start=page_start,
        page_end=page_start,
        parent_section_id=None,
    )


# ── Pure helpers ─────────────────────────────────────────────────────────


def test_group_whole_doc_proposals_buckets_by_section():
    props = [
        {"section": "5.1", "label": "A"},
        {"section": "5.1", "label": "B"},
        {"section": "5.2", "label": "C"},
        {"label": "D"},  # no section → __unassigned__
    ]
    groups = _group_whole_doc_proposals(props)
    assert set(groups.keys()) == {"5.1", "5.2", "__unassigned__"}
    assert len(groups["5.1"]) == 2
    assert len(groups["5.2"]) == 1
    assert len(groups["__unassigned__"]) == 1


def test_group_label_and_page_uses_lowest_page():
    props = [
        {"label": "A", "page": 15},
        {"label": "B", "page": 12},
        {"label": "C", "page": 18},
    ]
    label, page = _group_label_and_page("5.1.3", props, "Doc")
    assert label.startswith("5.1.3")
    assert page == 12


def test_group_label_and_page_unassigned_uses_doc_title():
    props = [{"label": "X"}]
    label, page = _group_label_and_page("__unassigned__", props, "MIL-STD-1")
    assert "MIL-STD-1" in label
    assert page is None


def test_group_label_and_page_handles_non_int_pages():
    # LLM sometimes emits strings instead of ints — those should be skipped.
    props = [
        {"label": "A", "page": "22"},
        {"label": "B", "page": 15},
    ]
    _, page = _group_label_and_page("5.1", props, "Doc")
    assert page == 15


def test_inject_page_markers_at_section_boundaries():
    text = "AAAA" + "BBBB" + "CCCC"  # 12 chars
    structure = _make_structure([
        _make_section("s0", "Intro", char_start=0, page_start=1),
        _make_section("s1", "Body", char_start=4, page_start=3),
        _make_section("s2", "Tail", char_start=8, page_start=7),
    ])
    result = _inject_page_markers(text, structure)
    assert "[PAGE 1]" in result
    assert "[PAGE 3]" in result
    assert "[PAGE 7]" in result
    # Content is preserved
    assert "AAAA" in result and "BBBB" in result and "CCCC" in result


def test_inject_page_markers_no_sections_is_noop():
    structure = _make_structure(sections=[])
    assert _inject_page_markers("hello world", structure) == "hello world"


def test_build_whole_doc_prompt_includes_required_context():
    system, user = _build_whole_doc_prompt(
        doc_title="Test Doc",
        toc_outline="1. Intro\n2. Body",
        paginated_text="[PAGE 1]\nContent here",
    )
    # Phase 1 prompt is scoped to artifact extraction (not graph placement)
    assert "phase 1" in system.lower()
    assert "artifact" in system.lower()
    assert "verbatim_quote" in system
    assert "tags" in system
    assert "[PAGE 1]" in user
    assert "1. Intro" in user
    # Must not ask the LLM for placement fields that Phase 2 owns
    assert "parent_label" not in system
    assert '"department"' not in system
    assert '"role_key"' not in system


# ── extract_whole_document ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_whole_document_calls_llm_and_parses():
    job = _make_job()
    structure = _make_structure([_make_section("s0", "Scope", 0, 1)])
    full_text = "[PAGE 1]\nScope. This standard applies to ..."

    # Phase 1 artifact-shape JSON (no placement fields like layer/parent_label)
    fake_llm_result = {
        "text": json.dumps([
            {"action": "create", "section": "1.1", "page": 1,
             "label": "Scope", "content": "...", "summary": "...",
             "verbatim_quote": "This standard applies to ...",
             "node_type": "standard", "tags": ["scope", "applicability"]},
        ]),
        "input_tokens": 12000,
        "output_tokens": 500,
        "cost_usd": 0.18,
    }

    with patch("app.services.document_extractor.llm_chat",
               return_value=fake_llm_result) as mock_llm:
        proposals, usage, raw = await extract_whole_document(
            job, full_text, structure,
        )

    mock_llm.assert_called_once()
    call_kwargs = mock_llm.call_args.kwargs
    assert call_kwargs["model"] == "opus"
    assert "[PAGE 1]" in call_kwargs["user_message"]
    assert len(proposals) == 1
    assert proposals[0]["section"] == "1.1"
    assert proposals[0]["node_type"] == "standard"
    assert usage["cost_usd"] == 0.18
    assert usage["input_tokens"] == 12000
    assert raw == fake_llm_result["text"]


@pytest.mark.asyncio
async def test_extract_whole_document_handles_empty_response():
    job = _make_job()
    structure = _make_structure([])
    fake_llm_result = {
        "text": "[]", "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.001,
    }
    with patch("app.services.document_extractor.llm_chat",
               return_value=fake_llm_result):
        proposals, usage, raw = await extract_whole_document(
            job, "short doc", structure,
        )
    assert proposals == []
    assert usage["cost_usd"] == 0.001
    assert raw == "[]"


# ── create_whole_doc_proposals ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_whole_doc_proposals_writes_artifact_state():
    job = _make_job()
    proposals = [
        {"action": "create", "section": "5.1", "page": 10, "label": "Metal A",
         "content": "foo", "summary": "s", "node_type": "standard",
         "verbatim_quote": "Metal A shall meet...", "tags": ["metal", "aluminum"]},
        {"action": "create", "section": "5.1", "page": 11, "label": "Metal B",
         "content": "bar", "summary": "s", "node_type": "standard",
         "verbatim_quote": "Metal B shall...", "tags": ["metal", "steel"]},
        {"action": "create", "section": "5.2", "page": 15, "label": "Ceramic",
         "content": "baz", "summary": "s", "node_type": "standard",
         "verbatim_quote": "Ceramic materials...", "tags": ["ceramic"]},
    ]
    sess = _FakeSession()

    pids = await create_whole_doc_proposals(
        job, proposals, existing_neurons=[], db=sess, doc_title="Test Doc",
    )

    # Two groups → two AutopilotProposal rows, all in state='artifact'
    proposals_added = [o for o in sess.added if isinstance(o, AutopilotProposal)]
    items_added = [o for o in sess.added if isinstance(o, ProposalItem)]
    assert len(proposals_added) == 2
    assert len(items_added) == 3
    assert len(pids) == 2

    for p in proposals_added:
        assert p.state == "artifact", f"expected state=artifact, got {p.state!r}"
        evidence = json.loads(p.gap_evidence_json)[0]
        assert evidence["source"] == "document_ingest"
        assert evidence["document"] == "TEST-STD-1.pdf"
        assert "page" in evidence
        # Phase 1 evidence carries the artifact's verbatim + node_type + tags
        assert "verbatim_quote" in evidence
        assert evidence["node_type"] == "standard"

    # ProposalItem specs are artifact-shape: no placement fields
    for it in items_added:
        spec = json.loads(it.neuron_spec_json)
        assert "parent_id" not in spec, "Phase 1 must leave parent unassigned"
        assert "layer" not in spec, "Phase 1 must leave layer unassigned"
        assert "department" not in spec, "Phase 1 must leave department unassigned"
        assert "role_key" not in spec, "Phase 1 must leave role_key unassigned"
        # Content/source-tracking fields still present
        assert spec["node_type"] == "standard"
        assert spec["source_origin"] == "document"
        assert it.target_neuron_id is None, "artifact rows have no parent target yet"


@pytest.mark.asyncio
async def test_create_whole_doc_proposals_records_page_per_group():
    """Page stored on each AutopilotProposal is the min page of its items."""
    job = _make_job()
    proposals = [
        {"action": "create", "section": "5.1", "page": 20, "label": "A",
         "content": "c", "summary": "s", "node_type": "knowledge"},
        {"action": "create", "section": "5.1", "page": 18, "label": "B",
         "content": "c", "summary": "s", "node_type": "knowledge"},
    ]
    sess = _FakeSession()
    await create_whole_doc_proposals(
        job, proposals, existing_neurons=[], db=sess, doc_title="D",
    )
    proposals_added = [o for o in sess.added if isinstance(o, AutopilotProposal)]
    assert len(proposals_added) == 1
    evidence = json.loads(proposals_added[0].gap_evidence_json)[0]
    assert evidence["page"] == 18


@pytest.mark.asyncio
async def test_create_whole_doc_proposals_skips_empty_batch():
    job = _make_job()
    sess = _FakeSession()
    pids = await create_whole_doc_proposals(
        job, proposals=[], existing_neurons=[], db=sess, doc_title="D",
    )
    assert pids == []
    assert sess.added == []


@pytest.mark.asyncio
async def test_create_whole_doc_proposals_skips_unknown_action():
    """Proposals with action not in {create, update} are filtered out."""
    job = _make_job()
    proposals = [
        {"action": "delete", "section": "5.1", "label": "X"},  # bogus action
        {"action": "create", "section": "5.2", "page": 3, "label": "Y",
         "content": "c", "summary": "s", "layer": 3},
    ]
    sess = _FakeSession()
    pids = await create_whole_doc_proposals(
        job, proposals, existing_neurons=[], db=sess, doc_title="D",
    )
    # Only the 5.2 group survives (5.1 had only a bogus-action proposal).
    assert len(pids) == 1


# ── dispatch threshold ──────────────────────────────────────────────────


def test_parser_extracts_array_from_prose():
    """Opus sometimes wraps the JSON in prose despite being told not to."""
    messy = (
        "Here's the extraction based on your document:\n\n"
        '[{"action":"create","label":"Foo","content":"bar"}, '
        '{"action":"create","label":"Baz","content":"qux"}]\n\n'
        "Let me know if you'd like me to expand on any of these."
    )
    out = _parse_llm_proposals(messy)
    assert len(out) == 2
    assert out[0]["label"] == "Foo"


def test_parser_handles_nested_brackets_in_strings():
    """A JSON string value can contain '[' chars — scanner must not be fooled."""
    text = '[{"action":"create","label":"Spec [rev A]","content":"see [page 5]"}]'
    out = _parse_llm_proposals(text)
    assert len(out) == 1
    assert "[rev A]" in out[0]["label"]


def test_parser_returns_empty_on_no_array():
    assert _parse_llm_proposals("I cannot do that.") == []


def test_find_json_array_span_returns_none_for_pure_prose():
    assert _find_json_array_span("just words, no brackets here") is None


def test_find_json_array_span_balances_brackets():
    # Nested array inside a string value should not confuse the scanner
    text = '[{"x": "a[b]c"}, {"y": 1}]'
    span = _find_json_array_span(text)
    assert span == text


def test_parser_falls_back_to_individual_objects():
    """When LLM emits fenced {...} blocks instead of an array (continuation
    behavior), the parser should still extract the objects."""
    messy = """Continuing from the cut-off point (5.4.1.4 onward):

```json
  {
    "action": "create",
    "section": "5.4.1.4",
    "page": 30,
    "label": "MAP Concurrent Engineering"
  }
```

```json
  {
    "action": "create",
    "section": "5.4.1.5",
    "page": 30,
    "label": "Inspection Criteria"
  }
```
"""
    out = _parse_llm_proposals(messy)
    assert len(out) == 2
    assert out[0]["section"] == "5.4.1.4"
    assert out[1]["section"] == "5.4.1.5"


def test_threshold_constant_is_reasonable():
    # Sanity guard: 600k chars ≈ 150k tokens, well below Opus's 200k window.
    assert 400_000 <= WHOLE_DOC_THRESHOLD_CHARS <= 800_000
