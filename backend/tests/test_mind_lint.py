"""Graph-lint primitives — pure-function coverage (no DB, no LLM)."""

import os

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.models import Neuron
from app.services import mind_lint


def _neuron(nid: int, label: str, content: str = "", department: str = "Projects",
            entities: list | None = None) -> Neuron:
    n = Neuron(id=nid, label=label, content=content, department=department,
               layer=3, node_type="lesson", entities=entities)
    return n


def test_pair_key_normalizes_order():
    assert mind_lint.pair_key(7, 3) == (3, 7)
    assert mind_lint.pair_key(3, 7) == (3, 7)
    with pytest.raises(AssertionError):
        mind_lint.pair_key(5, 5)


def test_content_hash_changes_with_content():
    a = _neuron(1, "same label", "fact one")
    b = _neuron(2, "same label", "fact two")
    assert mind_lint.content_hash(a) != mind_lint.content_hash(b)
    assert len(mind_lint.content_hash(a)) == 16


def test_token_jaccard_near_verbatim_vs_disjoint():
    a = _neuron(1, "corvus-mind backend runs on port 8005",
                "The corvus-mind tenant backend runs on port 8005.")
    b = _neuron(2, "corvus-mind backend port is 8005",
                "The corvus-mind backend runs on port 8005, not 8004.")
    c = _neuron(3, "meal planner uses sqlite",
                "FastAPI dinner planner persists to sqlite.")
    assert mind_lint.token_jaccard(a, b) > mind_lint.token_jaccard(a, c)
    assert mind_lint.token_jaccard(a, c) < mind_lint.LEXICAL_JACCARD_HIGH


def test_entity_overlap_none_when_missing():
    a = _neuron(1, "x", entities=["nvm", "node"])
    b = _neuron(2, "y", entities=None)
    assert mind_lint.entity_overlap(a, b) is None
    c = _neuron(3, "z", entities=["NVM", "chromebook"])
    assert mind_lint.entity_overlap(a, c) == pytest.approx(1 / 3)


def test_scope_lint_flags_machine_fact_under_projects():
    misfiled = _neuron(
        1, "Corvus frontend uses Node 22.22.0 via nvm",
        "Source ~/.config/nvm/nvm.sh and nvm use 22.22.0 before npm.",
        department="Projects")
    # the same fact filed under Environment is consistent — no flag
    fine = _neuron(2, misfiled.label, misfiled.content, department="Environment")
    # a genuinely repo-tied lesson under Projects stays put
    repo_tied = _neuron(
        3, "Corvus backend script execution",
        "cd ~/Projects/corvus/backend && TENANT_ID=corvus-mind "
        "PYTHONPATH=. venv/bin/python script.py", department="Projects")
    assert mind_lint.scope_lint_flag(misfiled)
    assert mind_lint.scope_lint_flag(fine) is None
    assert mind_lint.scope_lint_flag(repo_tied) is None


def test_duplicate_components_union_find():
    comps = mind_lint.duplicate_components(
        [1, 2, 3, 4, 5, 6], [(1, 2), (2, 3), (5, 6)])
    as_sets = sorted(tuple(c) for c in comps)
    assert as_sets == [(1, 2, 3), (5, 6)]


def test_codelivery_count():
    inclusion = {1: {10, 11, 12}, 2: {11, 12, 13}, 3: set()}
    assert mind_lint.codelivery_count(1, 2, inclusion) == 2
    assert mind_lint.codelivery_count(1, 3, inclusion) == 0
    assert mind_lint.codelivery_count(1, 99, inclusion) == 0
