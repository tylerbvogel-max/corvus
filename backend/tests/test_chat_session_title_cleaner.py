"""Unit tests for the chat-session title cleaner.

Pure string-processing function that strips preambles / quotes / trailing
punctuation from LLM-generated titles. Historical failure modes sampled
from real Haiku outputs that produced junk chat titles like "I appreciate
the question but" and "I can't answer this directly".
"""
from __future__ import annotations

import os

os.environ.setdefault("TENANT_ID", "corvus-aero")

from app.routers.chat_sessions import _clean_generated_title


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
