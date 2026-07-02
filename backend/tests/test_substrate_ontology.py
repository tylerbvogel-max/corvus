"""Substrate/ontology split (plat-substrate-ontology) — hermetic tests.

Covers: abstraction mapping, cold-start prior math + shrinkage handoff,
region-derived edge typing, and seeding-service pure helpers.
No DB, no network, no LLM.
"""

import json
import math

import numpy as np
import pytest

from app.models import (
    ABSTRACTION_BY_NODE_TYPE, KNOWLEDGE_ABSTRACTIONS,
    ABSTRACTION_STRUCTURAL, ABSTRACTION_ARTIFACT,
)
from app.services.scoring_engine import (
    ColdstartInputs, calc_coldstart_prior, calc_coldstart_term,
    calc_coldstart_term_batch, coldstart_shrinkage, compute_score,
)
from app.services.executor import _derive_edge_type
from app.services.seeding_service import (
    _knn_pairs, _parse_label_response, _region_proposal_items,
    _build_cluster_label_prompt,
)
from app.config import settings


# ── Abstraction axis ─────────────────────────────────────────────────

def test_abstraction_mapping_covers_org_layers():
    assert ABSTRACTION_BY_NODE_TYPE["department"] == ABSTRACTION_STRUCTURAL
    assert ABSTRACTION_BY_NODE_TYPE["role"] == ABSTRACTION_STRUCTURAL
    assert ABSTRACTION_BY_NODE_TYPE["task"] == "process"
    assert ABSTRACTION_BY_NODE_TYPE["system"] == "procedure"
    assert ABSTRACTION_BY_NODE_TYPE["decision"] == "principle"
    assert ABSTRACTION_BY_NODE_TYPE["output"] == ABSTRACTION_ARTIFACT


def test_structural_and_concept_are_not_knowledge():
    assert ABSTRACTION_STRUCTURAL not in KNOWLEDGE_ABSTRACTIONS
    assert "concept" not in KNOWLEDGE_ABSTRACTIONS
    assert len(KNOWLEDGE_ABSTRACTIONS) == 4


# ── Cold-start prior ─────────────────────────────────────────────────

def test_prior_ranks_authority():
    fresh = 10.0
    binding = calc_coldstart_prior("binding_standard", fresh, 0.5)
    informational = calc_coldstart_prior("informational", fresh, 0.5)
    unknown = calc_coldstart_prior(None, fresh, 0.5)
    assert binding > unknown > informational


def test_prior_ranks_freshness():
    recent = calc_coldstart_prior("regulatory", 7.0, 0.5)
    stale = calc_coldstart_prior("regulatory", 2000.0, 0.5)
    assert recent > stale


def test_prior_ranks_centrality():
    hub = calc_coldstart_prior("organizational", 100.0, 1.0)
    leaf = calc_coldstart_prior("organizational", 100.0, 0.0)
    assert hub > leaf


def test_prior_in_unit_range():
    for auth in ("binding_standard", "informational", None):
        for days in (0.0, 365.0, 10000.0, None):
            for cent in (0.0, 0.5, 1.0):
                p = calc_coldstart_prior(auth, days, cent)
                assert 0.0 <= p <= 1.0


def test_shrinkage_hands_off_to_posterior():
    """Prior dominates at zero invocations, decays out as firings accrue."""
    assert coldstart_shrinkage(0) == pytest.approx(1.0)
    strength = settings.coldstart_prior_strength
    assert coldstart_shrinkage(int(strength)) == pytest.approx(0.5)
    assert coldstart_shrinkage(1000) < 0.02


def test_coldstart_term_none_is_zero():
    assert calc_coldstart_term(None) == 0.0


def test_coldstart_term_sign():
    """High authority fresh hub -> positive; stale unanchored -> negative."""
    good = calc_coldstart_term(ColdstartInputs("binding_standard", 5.0, 0.9, 0))
    bad = calc_coldstart_term(ColdstartInputs("informational", 3000.0, 0.0, 0))
    assert good > 0
    assert bad < 0


def test_coldstart_batch_matches_scalar():
    inputs = [
        ("binding_standard", 5.0, 0.9, 0),
        ("informational", 3000.0, 0.0, 0),
        (None, None, 0.2, 50),
    ]
    batch = calc_coldstart_term_batch(
        authority_levels=[i[0] for i in inputs],
        freshness_days=np.array(
            [float("nan") if i[1] is None else i[1] for i in inputs]
        ),
        centrality=np.array([i[2] for i in inputs], dtype=np.float64),
        invocations=np.array([i[3] for i in inputs], dtype=np.float64),
    )
    for idx, (auth, days, cent, inv) in enumerate(inputs):
        scalar = calc_coldstart_term(ColdstartInputs(auth, days, cent, inv))
        assert batch[idx] == pytest.approx(scalar, abs=1e-9)


def test_compute_score_without_coldstart_unchanged():
    """Callers not passing coldstart get identical scores to before."""
    kwargs = dict(
        fires_in_window=3, avg_utility=0.6, dept_fires=10,
        dept_total_queries=100, age_queries=50, queries_since_last=5,
        keywords=["torque"], neuron_text="torque specification for fasteners",
    )
    without = compute_score(**kwargs)
    with_none = compute_score(**kwargs, coldstart=None)
    assert without.combined == with_none.combined


def test_compute_score_coldstart_boosts_authoritative_new():
    kwargs = dict(
        fires_in_window=0, avg_utility=0.5, dept_fires=0,
        dept_total_queries=0, age_queries=0, queries_since_last=0,
        keywords=["torque"], neuron_text="torque specification for fasteners",
    )
    baseline = compute_score(**kwargs)
    boosted = compute_score(
        **kwargs,
        coldstart=ColdstartInputs("binding_standard", 5.0, 0.9, 0),
    )
    assert boosted.combined > baseline.combined


def test_compute_score_never_negative():
    score = compute_score(
        fires_in_window=0, avg_utility=0.0, dept_fires=0,
        dept_total_queries=0, age_queries=1000, queries_since_last=10000,
        keywords=["zzz"], neuron_text="unrelated text entirely",
        coldstart=ColdstartInputs("informational", 5000.0, 0.0, 0),
    )
    assert score.combined >= 0.0


# ── Region-derived edge typing ───────────────────────────────────────

def test_edge_type_same_region_is_stellate():
    assert _derive_edge_type("Engineering", "Engineering") == "stellate"


def test_edge_type_cross_region_is_pyramidal():
    assert _derive_edge_type("Engineering", "Finance") == "pyramidal"


def test_edge_type_unknown_region_is_pyramidal():
    assert _derive_edge_type(None, "Engineering") == "pyramidal"
    assert _derive_edge_type(None, None) == "pyramidal"
    assert _derive_edge_type("", "") == "pyramidal"


# ── Seeding service pure helpers ─────────────────────────────────────

def _unit(vec):
    arr = np.array(vec, dtype=np.float32)
    return arr / np.linalg.norm(arr)


def test_knn_pairs_links_similar_neurons():
    ids = [10, 20, 30]
    matrix = np.stack([
        _unit([1.0, 0.0, 0.0]),
        _unit([0.95, 0.05, 0.0]),   # near neuron 10
        _unit([0.0, 1.0, 0.0]),     # orthogonal
    ])
    pairs = _knn_pairs(ids, matrix, k=2, min_similarity=0.5)
    assert (10, 20) in pairs
    assert (10, 30) not in pairs
    assert (20, 30) not in pairs


def test_knn_pairs_bounded_and_deduped():
    ids = [1, 2]
    matrix = np.stack([_unit([1.0, 0.0]), _unit([1.0, 0.01])])
    pairs = _knn_pairs(ids, matrix, k=5, min_similarity=0.5)
    assert list(pairs.keys()) == [(1, 2)]


def test_knn_pairs_empty_graph():
    assert _knn_pairs([], np.zeros((0, 384), dtype=np.float32), 5, 0.3) == {}


def test_label_response_parses_clean_json():
    raw = json.dumps([
        {"cluster_id": 0, "region_label": "Quality Assurance", "rationale": "QA items"},
        {"cluster_id": 1, "region_label": "Supply Chain", "rationale": "vendor items"},
    ])
    labels = _parse_label_response(raw, {0, 1})
    assert labels[0]["region_label"] == "Quality Assurance"
    assert labels[1]["region_label"] == "Supply Chain"


def test_label_response_strips_code_fence_and_filters_ids():
    raw = '```json\n[{"cluster_id": 0, "region_label": "Ops", "rationale": "r"},' \
          ' {"cluster_id": 9, "region_label": "Ghost", "rationale": "r"}]\n```'
    labels = _parse_label_response(raw, {0})
    assert set(labels.keys()) == {0}


def test_label_response_garbage_is_empty():
    assert _parse_label_response("not json at all", {0}) == {}
    assert _parse_label_response('{"a": 1}', {0}) == {}


def test_region_proposal_items_shape():
    cluster = {"cluster_id": 3, "neuron_ids": [7, 8, 9]}
    items = _region_proposal_items(cluster, "Field Service")
    assert len(items) == 4  # 1 create root + 3 member updates
    root = items[0]
    assert root.action == "create"
    spec = json.loads(root.neuron_spec_json)
    assert spec["node_type"] == "department"
    assert spec["abstraction_type"] == "structural"
    assert spec["department"] == "Field Service"
    for item, nid in zip(items[1:], [7, 8, 9]):
        assert item.action == "update"
        assert item.field == "department"
        assert item.new_value == "Field Service"
        assert item.target_neuron_id == nid


def test_cluster_label_prompt_is_bounded():
    clusters = [{
        "cluster_id": 0,
        "neuron_ids": list(range(100)),
        "suggested_label": "weld + inspection + certification",
        "sample_labels": [f"item {i}" for i in range(50)],
    }]
    system, user = _build_cluster_label_prompt(clusters)
    assert "JSON array" in system
    digest = json.loads(user)
    assert len(digest[0]["sample_items"]) <= 10
