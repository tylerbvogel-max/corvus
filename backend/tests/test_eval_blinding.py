"""Judge blinding (roadmap meas-blind-ab).

The comparative eval judge must never learn which model produced which
answer: producer identity in the prompt lets brand/self-preference bias the
scores (found live 2026-07-09 — answers were headed "Answer A (Haiku +
Neurons @ 8K)"). Blinding is by construction: bare letters in the prompt,
letter->slot resolution stays caller-side in answer_map.
"""
from app.routers.query import _build_eval_prompts
from app.schemas import SlotResult


def _slot(model: str, label: str | None, response: str) -> SlotResult:
    return SlotResult(
        mode=f"{model}_neuron", model=model, neurons=True, response=response,
        input_tokens=100, output_tokens=100, cost_usd=0.01,
        token_budget=8000, label=label,
    )


_SLOTS = [
    _slot("haiku", "Haiku + Neurons @ 8K", "First answer body."),
    _slot("sonnet", "My Custom Sonnet Run", "Second answer body."),
]

_IDENTITY_MARKERS = ("haiku", "sonnet", "opus", "neurons @", "custom")


def test_judge_prompt_carries_no_model_identity():
    eval_system, eval_prompt, _ = _build_eval_prompts("Q?", _SLOTS)
    haystack = (eval_system + eval_prompt).lower()
    for marker in _IDENTITY_MARKERS:
        assert marker not in haystack, f"judge prompt leaks producer identity: {marker!r}"
    assert "Answer A:" in eval_prompt and "Answer B:" in eval_prompt


def test_domain_knowledge_variant_is_equally_blind():
    eval_system, eval_prompt, _ = _build_eval_prompts(
        "Q?", _SLOTS, domain_knowledge="AS9100D requires documented procedures.",
    )
    haystack = (eval_system + eval_prompt).lower()
    for marker in _IDENTITY_MARKERS:
        assert marker not in haystack, f"domain judge prompt leaks identity: {marker!r}"
    # The domain facts themselves must still be present
    assert "AS9100D" in eval_system


def test_answer_map_resolves_letters_caller_side():
    _, _, answer_map = _build_eval_prompts("Q?", _SLOTS)
    assert [letter for letter, _ in answer_map] == ["A", "B"]
    assert answer_map[0][1].model == "haiku"
    assert answer_map[1][1].model == "sonnet"


def test_reversed_presentation_relabels_by_position():
    """Counterbalancing depends on letters tracking POSITION, not identity:
    the reversed pass must present the other answer as 'Answer A'."""
    _, fwd_prompt, _ = _build_eval_prompts("Q?", _SLOTS)
    _, rev_prompt, rev_map = _build_eval_prompts("Q?", list(reversed(_SLOTS)))
    assert "Answer A:\nFirst answer body." in fwd_prompt
    assert "Answer A:\nSecond answer body." in rev_prompt
    assert rev_map[0][1].model == "sonnet"
