"""Tests for frequency-hopped citation grounding (anti-hallucination exit layer)."""

import re

from app.config import settings
from app.models import Neuron
from app.services.scoring_engine import NeuronScoreBreakdown
from app.services.prompt_assembler import assemble_prompt
from app.services.citation_hopping import (
    HopMap,
    mint_hop_map,
    extract_citation_tokens,
    verify_citations,
    strip_hallucinated,
    repair_instruction,
)


def _make_neuron(nid, label, content=None, department="Engineering", role_key="mech_eng", layer=5):
    n = Neuron()
    n.id = nid
    n.label = label
    n.content = content
    n.summary = content
    n.department = department
    n.role_key = role_key
    n.layer = layer
    return n


def _make_score(neuron_id, combined=0.5):
    return NeuronScoreBreakdown(
        neuron_id=neuron_id, burst=0.5, impact=0.5,
        precision=0.5, novelty=0.5, recency=0.5, relevance=0.5, combined=combined,
    )


def _token_pattern():
    return re.compile("^" + re.escape(settings.citation_hop_prefix) + r"[0-9A-F]{%d}$" % settings.citation_hop_hex_width)


# ---- minting ----

def test_mint_assigns_unique_well_formed_tokens():
    hop = mint_hop_map([1, 2, 3])
    assert set(hop.token_by_neuron.keys()) == {1, 2, 3}
    tokens = list(hop.token_by_neuron.values())
    assert len(set(tokens)) == 3, "tokens must be unique within a query"
    pat = _token_pattern()
    for t in tokens:
        assert pat.match(t), f"token {t!r} must match {settings.citation_hop_prefix}<hex>"
    # reverse map is a consistent inverse
    for nid, tok in hop.token_by_neuron.items():
        assert hop.neuron_by_token[tok] == nid


def test_mint_dedups_repeated_ids():
    hop = mint_hop_map([7, 7, 9])
    assert set(hop.token_by_neuron.keys()) == {7, 9}
    assert len(hop.neuron_by_token) == 2


def test_mint_rotates_per_query():
    """Frequency hopping: the same neurons get fresh keys every query."""
    a = mint_hop_map([1, 2, 3])
    b = mint_hop_map([1, 2, 3])
    assert a.token_by_neuron != b.token_by_neuron


# ---- extraction ----

def test_extract_bracketed_bare_case_insensitive_dedup():
    pfx = settings.citation_hop_prefix
    text = (
        f"Widget A requires B [{pfx}AB12CD]. See also {pfx}ab12cd and [{pfx}EE33FF]. "
        "Numbers like [1] and F-16 must not match."
    )
    got = extract_citation_tokens(text)
    assert set(got) == {f"{pfx}AB12CD", f"{pfx}EE33FF"}
    # case-insensitive collapse: the lower-case repeat did not create a duplicate
    assert len(got) == 2


def test_extract_ignores_aircraft_designations():
    # The whole point of the FQ- prefix: F-16 / F/A-18 never match.
    assert extract_citation_tokens("The F-16 and F-35 and F/A-18 fly.") == []


# ---- verification ----

def test_verify_clean_pass():
    hop = mint_hop_map([10, 20])
    t10, t20 = hop.token_by_neuron[10], hop.token_by_neuron[20]
    result = verify_citations([t10, t20], hop)
    assert result.ok is True
    assert result.hallucinated == []
    assert result.cited_neuron_ids == [10, 20]


def test_verify_detects_fabricated_key():
    hop = mint_hop_map([10])
    valid = hop.token_by_neuron[10]
    fake = f"{settings.citation_hop_prefix}DEADBE"
    result = verify_citations([valid, fake], hop)
    assert result.ok is False
    assert result.hallucinated == [fake]
    assert result.cited_neuron_ids == [10], "only the real key resolves to a neuron"


def test_verify_require_all_missing():
    hop = mint_hop_map([1, 2])
    t1 = hop.token_by_neuron[1]
    result = verify_citations([t1], hop, require_all=True)
    assert result.ok is False
    assert result.missing == [hop.token_by_neuron[2]]


def test_verify_require_all_off_allows_partial():
    hop = mint_hop_map([1, 2])
    t1 = hop.token_by_neuron[1]
    result = verify_citations([t1], hop, require_all=False)
    assert result.ok is True
    assert result.missing == []


# ---- strip / repair ----

def test_strip_removes_only_fabricated():
    hop = mint_hop_map([5])
    valid = hop.token_by_neuron[5]
    fake = f"{settings.citation_hop_prefix}BADBAD"
    text = f"Real claim [{valid}]. Fake claim [{fake}]."
    out = strip_hallucinated(text, [fake])
    assert fake not in out
    assert valid in out


def test_repair_instruction_lists_valid_and_bad():
    hop = mint_hop_map([1, 2])
    fake = f"{settings.citation_hop_prefix}000000"
    instr = repair_instruction(hop, [fake])
    assert fake in instr
    for tok in hop.tokens():
        assert tok in instr


# ---- assembler entry-layer integration ----

def test_assembler_renders_hop_tokens_and_instruction():
    neurons = {1: _make_neuron(1, "Stress Analysis", content="Run FEA for load cases.")}
    scores = [_make_score(1, 0.8)]
    token = f"{settings.citation_hop_prefix}1A2B3C"
    prompt = assemble_prompt("engineering", scores, neurons, citation_tokens={1: token})
    assert f"[{token}]" in prompt
    assert "unique to this answer" in prompt  # hop-specific citation instruction
    # the neuron content is still present
    assert "Stress Analysis" in prompt


def test_assembler_default_is_numeric_backward_compatible():
    neurons = {1: _make_neuron(1, "Stress Analysis", content="Run FEA for load cases.")}
    scores = [_make_score(1, 0.8)]
    prompt = assemble_prompt("engineering", scores, neurons)  # no citation_tokens
    assert "[1] " in prompt
    assert "bracketed number" in prompt  # numeric citation instruction
    assert settings.citation_hop_prefix not in prompt
