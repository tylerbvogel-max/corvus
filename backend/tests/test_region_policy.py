"""Per-region config over shared substrate (plat-region-config) — hermetic.

Covers: weight resolution/merging, ACL predicate + SQL clause, per-region
weight arrays in the vectorized scorer, and per-region write-gate overlay.
"""

import numpy as np
import pytest

from app.config import settings
from app.services.region_policy import (
    RequesterContext, WEIGHT_KEYS, acl_sql_clause, default_weights,
    requester_can_see, resolve_scoring_weights, restricted_regions,
)
from app.services.neuron_service import NeuronCandidate, _weight_arrays
from app.services.write_gate import WriteGatePolicy, _policy_from_dict


from types import MappingProxyType

POLICIES = MappingProxyType({
    "Manufacturing & Operations": {
        "scoring_weights": {"weight_recency": 0.30, "weight_relevance": 0.40},
        "acl": {},
    },
    "Legal": {
        "scoring_weights": {"weight_impact": 0.30, "weight_recency": 0.02},
        "acl": {"visibility": "restricted"},
    },
    "HR": {
        "scoring_weights": {},
        "acl": {"visibility": "restricted"},
    },
})


# ── Weight resolution ────────────────────────────────────────────────

def test_default_weights_match_settings():
    weights = default_weights()
    assert weights["weight_relevance"] == settings.weight_relevance
    assert set(weights) == set(WEIGHT_KEYS)


def test_region_overrides_merge_partially():
    weights = resolve_scoring_weights("Manufacturing & Operations", POLICIES)
    assert weights["weight_recency"] == 0.30       # overridden
    assert weights["weight_relevance"] == 0.40     # overridden
    assert weights["weight_impact"] == settings.weight_impact  # inherited


def test_unpolicied_region_gets_defaults():
    assert resolve_scoring_weights("Engineering", POLICIES) == default_weights()
    assert resolve_scoring_weights(None, POLICIES) == default_weights()


# ── ACL predicate ────────────────────────────────────────────────────

def test_restricted_regions_extraction():
    assert set(restricted_regions(POLICIES)) == {"Legal", "HR"}


def test_unrestricted_requester_sees_everything():
    restricted = {"Legal"}
    assert requester_can_see(None, "Legal", None, restricted)
    assert requester_can_see(None, "Legal", "restricted", restricted)


def test_privileged_reader_sees_across_regions():
    reconciler = RequesterContext(principal="reconciler", regions=(), privileged=True)
    assert requester_can_see(reconciler, "Legal", None, {"Legal"})


def test_member_sees_own_restricted_region():
    legal_user = RequesterContext(principal="u1", regions=("Legal",))
    assert requester_can_see(legal_user, "Legal", None, {"Legal"})


def test_nonmember_cannot_see_restricted_region():
    mfg_user = RequesterContext(principal="u2", regions=("Manufacturing & Operations",))
    assert not requester_can_see(mfg_user, "Legal", None, {"Legal"})
    # but open regions remain visible
    assert requester_can_see(mfg_user, "Engineering", None, {"Legal"})


def test_per_neuron_override_beats_region_default():
    mfg_user = RequesterContext(principal="u2", regions=("Manufacturing & Operations",))
    # neuron explicitly restricted in an open region
    assert not requester_can_see(mfg_user, "Engineering", "restricted", {"Legal"})
    # neuron explicitly open in a restricted region
    assert requester_can_see(mfg_user, "Legal", "open", {"Legal"})


def test_acl_sql_clause_forms():
    params: dict = {}
    assert acl_sql_clause(None, ["Legal"], params) == "TRUE"
    assert acl_sql_clause(
        RequesterContext(privileged=True), ["Legal"], params,
    ) == "TRUE"
    assert acl_sql_clause(RequesterContext(regions=("X",)), [], params) == "TRUE"

    clause = acl_sql_clause(RequesterContext(regions=("Legal",)), ["Legal", "HR"], params)
    assert "acl_restricted_regions" in params
    assert params["acl_requester_regions"] == ["Legal"]
    assert "visibility" in clause and "department" in clause


# ── Vectorized per-region weights ────────────────────────────────────

def _candidate(cid, region):
    return NeuronCandidate(
        id=cid, label=f"n{cid}", summary=None, department=region, role_key=None,
        avg_utility=0.5, invocations=0, created_at_query_count=0,
    )


def test_weight_arrays_none_without_policies():
    assert _weight_arrays([_candidate(1, "Legal")], None) is None


def test_weight_arrays_per_candidate():
    candidates = [
        _candidate(1, "Manufacturing & Operations"),
        _candidate(2, "Legal"),
        _candidate(3, "Engineering"),  # no policy -> defaults
    ]
    region_weights = {
        region: resolve_scoring_weights(region, POLICIES)
        for region in ("Manufacturing & Operations", "Legal", "Engineering")
    }
    arrays = _weight_arrays(candidates, region_weights)
    assert arrays is not None
    np.testing.assert_allclose(arrays["weight_recency"], [0.30, 0.02, settings.weight_recency])
    np.testing.assert_allclose(arrays["weight_impact"],
                               [settings.weight_impact, 0.30, settings.weight_impact])


# ── Per-region write-gate overlay ────────────────────────────────────

def test_write_gate_overlay_inherits_base():
    base = WriteGatePolicy(mode="tiered", auto_commit_max_authority="guidance",
                           require_guardrails_pass=True, min_confidence=0.6)
    overlaid = _policy_from_dict({"min_confidence": 0.9}, base)
    assert overlaid.mode == "tiered"
    assert overlaid.auto_commit_max_authority == "guidance"
    assert overlaid.min_confidence == 0.9


def test_write_gate_overlay_can_force_manual():
    base = WriteGatePolicy(mode="tiered")
    overlaid = _policy_from_dict({"mode": "manual"}, base)
    assert overlaid.mode == "manual"


def test_write_gate_rejects_bad_mode():
    with pytest.raises(ValueError):
        _policy_from_dict({"mode": "yolo"})
