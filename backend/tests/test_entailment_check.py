"""Opt-in claim-entailment check (grounding backlog §6.4).

Pure-function coverage (claim extraction, verdict parsing, prompt build) plus
a mocked end-to-end run_entailment_check — no real DB or LLM.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
import app.services.entailment_check as ec
from app.services.citation_hopping import HopMap


def _token(suffix: str) -> str:
    return f"{settings.citation_hop_prefix}{suffix}"


# ---- extract_cited_claims ----

def test_extract_cited_claims_pairs_sentence_with_tokens():
    t1, t2 = _token("AAAA11"), _token("BBBB22")
    answer = (
        f"Design reviews are mandatory before CDR [{t1}]. "
        "This sentence cites nothing. "
        f"Weld inspection follows the qualified procedure [{t2}]."
    )
    claims = ec.extract_cited_claims(answer, max_claims=10)
    assert len(claims) == 2
    assert claims[0][1] == [t1]
    assert t1 not in claims[0][0], "tokens must be stripped from the claim text"
    assert claims[0][0].startswith("Design reviews are mandatory")


def test_extract_cited_claims_respects_cap_and_min_length():
    t = _token("CCCC33")
    many = " ".join(f"Sentence number {i} makes a substantive claim [{t}]." for i in range(20))
    assert len(ec.extract_cited_claims(many, max_claims=5)) == 5
    assert ec.extract_cited_claims(f"Short [{t}].", max_claims=5) == []


def test_extract_cited_claims_no_tokens():
    assert ec.extract_cited_claims("No citations anywhere in this answer.", 10) == []


def test_extract_cited_claims_skips_colon_lead_ins():
    t = _token("ABCD99")
    answer = f"Before CDR gates, you must demonstrate the following [{t}]:"
    assert ec.extract_cited_claims(answer, 10) == []


# ---- _parse_verdicts ----

def test_parse_verdicts_valid_json_with_prose_padding():
    raw = 'Here you go:\n{"verdicts": [{"pair": 0, "supported": true, "reason": "matches"}, {"pair": 1, "supported": false, "reason": "not stated"}]}'
    v = ec._parse_verdicts(raw, 2)
    assert v is not None
    assert v[0]["supported"] is True
    assert v[1]["supported"] is False


def test_parse_verdicts_missing_pair_yields_empty_dict():
    raw = json.dumps({"verdicts": [{"pair": 1, "supported": True, "reason": "ok"}]})
    v = ec._parse_verdicts(raw, 2)
    assert v is not None
    assert v[0] == {}, "unjudged pair must degrade to empty verdict"
    assert v[1]["supported"] is True


def test_parse_verdicts_garbage_returns_none():
    assert ec._parse_verdicts("no json here", 1) is None
    assert ec._parse_verdicts('{"verdicts": "not-a-list"}', 1) is None
    assert ec._parse_verdicts("{broken json", 1) is None


# ---- _build_judge_message ----

def test_build_judge_message_numbers_pairs_and_lists_sources():
    msg = ec._build_judge_message([
        ("Claim one text", [("Src A", "content a")]),
        ("Claim two text", [("Src B", "content b"), ("Src C", "content c")]),
    ])
    assert "=== Pair 0 ===" in msg and "=== Pair 1 ===" in msg
    assert "CLAIM: Claim one text" in msg
    assert "SOURCE (Src B): content b" in msg
    assert "SOURCE (Src C): content c" in msg


# ---- run_entailment_check (mocked) ----

def _hop_map(token: str, neuron_id: int = 42) -> HopMap:
    return HopMap(
        token_by_neuron={neuron_id: token},
        neuron_by_token={token: neuron_id},
    )


def test_run_entailment_check_no_answer():
    result = asyncio.run(ec.run_entailment_check(AsyncMock(), "", None))
    assert result["status"] == "no_answer"
    assert result["checked"] == 0


def test_run_entailment_check_no_hop_session():
    db = AsyncMock()
    with patch.object(ec, "_load_hop_map_for_query", AsyncMock(return_value=None)):
        result = asyncio.run(ec.run_entailment_check(db, "Some answer text.", 7))
    assert result["status"] == "no_hop_session"


def test_run_entailment_check_happy_path():
    token = _token("DDDD44")
    answer = f"Torque values must follow the calibrated wrench table [{token}]."
    verdict_payload = json.dumps({"verdicts": [
        {"pair": 0, "supported": False, "reason": "source describes inspection, not torque"},
    ]})
    db = AsyncMock()

    async def judge_after_release(*_args, **_kwargs):
        assert db.commit.await_count == 1, \
            "entailment judge must not run inside the source-read transaction"
        return {"text": verdict_payload, "cost_usd": 0.003}

    llm = AsyncMock(side_effect=judge_after_release)
    sources = {token: ("Wrench Calibration", "calibration schedule content")}
    with patch.object(ec, "_load_hop_map_for_query", AsyncMock(return_value=_hop_map(token))), \
         patch.object(ec, "_load_source_texts", AsyncMock(return_value=sources)), \
         patch.object(ec, "llm_chat", llm):
        result = asyncio.run(ec.run_entailment_check(db, answer, 7))
    assert result["status"] == "ok"
    assert result["checked"] == 1
    assert result["unsupported_count"] == 1
    assert result["results"][0]["supported"] is False
    assert result["results"][0]["sources"] == ["Wrench Calibration"]
    assert result["cost_usd"] == 0.003


def test_run_entailment_check_llm_failure_degrades():
    token = _token("EEEE55")
    answer = f"Claims must be checked against sources [{token}]."
    sources = {token: ("Src", "content")}
    with patch.object(ec, "_load_hop_map_for_query", AsyncMock(return_value=_hop_map(token))), \
         patch.object(ec, "_load_source_texts", AsyncMock(return_value=sources)), \
         patch.object(ec, "llm_chat", AsyncMock(side_effect=RuntimeError("CLI timeout"))):
        result = asyncio.run(ec.run_entailment_check(AsyncMock(), answer, 7))
    assert result["status"] == "llm_error"
    assert result["checked"] == 0


def test_run_entailment_check_parse_failure_degrades():
    token = _token("FFFF66")
    answer = f"Claims must be checked against sources [{token}]."
    sources = {token: ("Src", "content")}
    with patch.object(ec, "_load_hop_map_for_query", AsyncMock(return_value=_hop_map(token))), \
         patch.object(ec, "_load_source_texts", AsyncMock(return_value=sources)), \
         patch.object(ec, "llm_chat", AsyncMock(return_value={"text": "not json", "cost_usd": 0.001})):
        result = asyncio.run(ec.run_entailment_check(AsyncMock(), answer, 7))
    assert result["status"] == "parse_error"


def test_load_source_texts_prefers_content_and_truncates(monkeypatch):
    monkeypatch.setattr(settings, "entailment_source_chars", 10)
    token = _token("ABAB77")
    neuron = MagicMock()
    neuron.id = 42
    neuron.label = "Neuron Label"
    neuron.content = "x" * 100
    neuron.summary = "summary"
    scalars = MagicMock()
    scalars.scalars.return_value = [neuron]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scalars)
    out = asyncio.run(ec._load_source_texts(db, _hop_map(token)))
    assert out[token][0] == "Neuron Label"
    assert out[token][1] == "x" * 10, "excerpt must be truncated to the configured cap"
