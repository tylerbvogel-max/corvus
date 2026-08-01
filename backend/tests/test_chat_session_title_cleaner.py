"""Unit tests for the chat-session title cleaner.

Pure string-processing function that strips preambles / quotes / trailing
punctuation from LLM-generated titles. Historical failure modes sampled
from real Haiku outputs that produced junk chat titles like "I appreciate
the question but" and "I can't answer this directly".
"""
from __future__ import annotations

import os

os.environ.setdefault("TENANT_ID", "corvus-mind")

from app.routers.chat_sessions import (
    _clean_generated_title,
    _fallback_title_from_message,
    _looks_like_preamble,
)


# ── Happy path ──

def test_clean_passes_through_a_well_formed_title():
    assert _clean_generated_title("AS9100D Key Requirements") == "AS9100D Key Requirements"


def test_clean_strips_surrounding_quotes():
    assert _clean_generated_title('"Cost Allocation Process"') == "Cost Allocation Process"
    assert _clean_generated_title("'Travel Policy'") == "Travel Policy"


def test_clean_strips_trailing_punctuation():
    assert _clean_generated_title("Remote Work Policy.") == "Remote Work Policy"
    assert _clean_generated_title("Export Controls!") == "Export Controls"
    assert _clean_generated_title("FAR vs DFARS Compliance:") == "FAR vs DFARS Compliance"


def test_clean_takes_only_the_first_line():
    assert _clean_generated_title("Travel Policy\n(for international trips)") == "Travel Policy"


# ── Preamble stripping — the core failure modes ──

def test_clean_strips_i_appreciate_preamble():
    out = _clean_generated_title("I appreciate the question but the topic is Travel Policy")
    assert "appreciate" not in out.lower()
    assert "Travel Policy" in out or "Policy" in out


def test_clean_strips_i_cant_answer_preamble():
    out = _clean_generated_title("I can't answer this directly, but the topic is Export Controls")
    assert "can't" not in out.lower() and "cannot" not in out.lower()
    assert "Export Controls" in out


def test_clean_strips_title_label_prefix():
    assert _clean_generated_title("Title: AS9100D Requirements") == "AS9100D Requirements"
    assert _clean_generated_title("Topic: Cost Allocation") == "Cost Allocation"
    assert _clean_generated_title("Subject: Compliance Review") == "Compliance Review"


def test_clean_strips_the_topic_is_lead_in():
    assert _clean_generated_title("The topic is cost allocation process") == "cost allocation process"


def test_clean_strips_here_is_the_title_lead_in():
    out = _clean_generated_title("Here is the title: Supplier Onboarding")
    assert out == "Supplier Onboarding"


def test_clean_strips_i_think_preamble():
    out = _clean_generated_title("I think the user is asking about Travel Policy")
    assert "think" not in out.lower()


# ── Edge cases ──

def test_clean_empty_input_returns_empty():
    assert _clean_generated_title("") == ""


def test_clean_whitespace_only_returns_empty():
    assert _clean_generated_title("   \n  ") == ""


def test_clean_caps_at_six_words():
    # A ten-word output gets trimmed to six.
    long_title = "One Two Three Four Five Six Seven Eight Nine Ten"
    assert _clean_generated_title(long_title) == "One Two Three Four Five Six"


def test_clean_handles_markdown_wrapped_output():
    assert _clean_generated_title("`Remote Work Policy`") == "Remote Work Policy"


def test_clean_handles_full_preamble_then_title():
    # Combined preamble + label + quote — the "everything is wrong" sample.
    raw = 'I appreciate the question but Title: "Export Controls Overview"'
    out = _clean_generated_title(raw)
    # Should end up as something like "Export Controls Overview" (preamble
    # + label + quotes all stripped).
    assert "appreciate" not in out.lower()
    assert "Title" not in out
    assert '"' not in out
    assert "Export Controls" in out


# ── Rejection guard (_looks_like_preamble) ──────────────────────────────

def test_looks_like_preamble_catches_i_pronoun():
    assert _looks_like_preamble("I notice the Corvus neuron graph") is True


def test_looks_like_preamble_catches_we_pronoun():
    assert _looks_like_preamble("We don't have access") is True


def test_looks_like_preamble_catches_sorry():
    assert _looks_like_preamble("Sorry, this is beyond my scope") is True


def test_looks_like_preamble_catches_as_an_ai():
    assert _looks_like_preamble("As an AI assistant I cannot") is True


def test_looks_like_preamble_catches_let_me():
    assert _looks_like_preamble("Let me help clarify") is True


def test_looks_like_preamble_passes_real_titles():
    assert _looks_like_preamble("AS9100D Key Requirements") is False
    assert _looks_like_preamble("Cost Allocation Process") is False
    assert _looks_like_preamble("Export Controls Overview") is False


def test_looks_like_preamble_rejects_bare_article_lead():
    # "The X Y" at title position is usually a preamble remnant like
    # "The topic is X" that didn't fully get stripped.
    assert _looks_like_preamble("The topic you mentioned") is True


def test_looks_like_preamble_empty_title_rejected():
    assert _looks_like_preamble("") is True
    assert _looks_like_preamble("   ") is True


# ── Fallback title extractor ────────────────────────────────────────────

def test_fallback_extracts_content_words_from_question():
    t = _fallback_title_from_message("What are the key requirements of AS9100D?")
    # "what, are, the, of" are stopwords; remaining = "key requirements AS9100D?"
    assert "AS9100D" in t or "key" in t.lower()
    assert "what" not in t.lower() and "the" not in t.lower()


def test_fallback_caps_at_six_words():
    long_msg = "explain the details of our supplier onboarding compliance verification procedures exhaustively"
    t = _fallback_title_from_message(long_msg)
    assert len(t.split()) <= 6


def test_fallback_on_greeting_uses_general_inquiry():
    assert _fallback_title_from_message("hi") == "General Inquiry"
    # "How are you" — all stopwords, should fall through.
    assert _fallback_title_from_message("how are you") == "General Inquiry"


# ── New preamble patterns (expanded coverage) ───────────────────────────

def test_clean_strips_i_dont_have_preamble():
    out = _clean_generated_title("I don't have access to that, but the topic is Export Controls")
    assert "don't" not in out.lower()
    assert "Export Controls" in out


def test_clean_strips_as_an_ai_preamble():
    out = _clean_generated_title("As an AI, I cannot judge, but the topic is Compliance Review")
    assert "AI" not in out
    assert "Compliance Review" in out


def test_clean_strips_i_notice_preamble():
    out = _clean_generated_title("I notice you're asking about Travel Policy")
    assert "notice" not in out.lower()
    assert "Travel Policy" in out
