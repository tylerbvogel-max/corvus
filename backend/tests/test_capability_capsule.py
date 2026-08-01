"""Capability Capsule trust boundary and harness portability tests."""

import copy
import json
from types import SimpleNamespace

from app.services import capability_capsule as cc
from app.services import skill_projection


def _capsule(memories=None, skills=None):
    value = {
        "schema": cc.SCHEMA, "schema_version": cc.SCHEMA_VERSION,
        "capsule_id": "cap_test", "source_instance": "test",
        "identity": ["mem_identity"], "governance": ["mem_policy"],
        "memories": memories or [{"memory_id": "mem_identity"}],
        "skills": skills or [{"name": "mind-test", "requires": ["shell", "hooks"]}],
        "capability_contract": {"semantic_capabilities": ["filesystem", "hooks", "mcp"],
                                "lifecycle_events": ["session_start", "prompt_submit", "pre_tool", "post_tool", "stop"]},
        "tool_semantics": {"vocabulary": "corvus.semantic-tools.v1"},
        "harness_profiles": {},
    }
    return cc.seal(value, signing_key="test-key")


def test_sealed_capsule_round_trip():
    capsule = _capsule()
    assert cc.verify(capsule, signing_key="test-key") == {
        "valid": True, "errors": [],
        "digest": capsule["integrity"]["digest"], "signed": True,
    }


def test_tampering_is_detected():
    capsule = _capsule()
    capsule["memories"][0]["memory_id"] = "tampered"
    result = cc.verify(capsule, signing_key="test-key")
    assert not result["valid"]
    assert "digest mismatch" in result["errors"]


def test_wrong_signing_key_is_detected():
    result = cc.verify(_capsule(), signing_key="wrong")
    assert result["errors"] == ["signature mismatch"]


def test_stable_memory_id_ignores_source_database_id():
    item = {"kind": "lesson", "label": "x", "content": "y",
            "portability_scope": "user", "project": None,
            "source_neuron_id": 1}
    other = {**item, "source_neuron_id": 999}
    assert cc.stable_memory_id(item) == cc.stable_memory_id(other)


def test_stable_id_survives_evidence_append_but_content_hash_changes():
    item = {"kind": "lesson", "label": "x", "content": "fact",
            "summary": "fact", "portability_scope": "project", "project": "corvus"}
    gated = {**item, "content": "fact\n\nEvidence: capsule abc"}
    assert cc.stable_memory_id(item) == cc.stable_memory_id(gated)
    assert cc.memory_content_hash(item) != cc.memory_content_hash(gated)


def test_portability_scope_is_conservative():
    assert cc.portability_scope(SimpleNamespace(department="User")) == "user"
    assert cc.portability_scope(SimpleNamespace(department="Environment")) == "machine"
    assert cc.portability_scope(SimpleNamespace(department="new-region")) == "organization"


def test_profiles_declare_three_harnesses():
    profiles = cc.load_harness_profiles()
    assert set(profiles) == {"claude-code", "codex", "opencode"}
    assert all("semantic_capabilities" in x for x in profiles.values())


def test_reconcile_reports_degraded_capabilities(monkeypatch):
    monkeypatch.setenv(cc.HMAC_ENV, "test-key")
    capsule = _capsule(skills=[{"name": "x", "requires": ["ask_user", "telepathy"]}])
    plan = cc.reconcile(capsule, {"mem_identity"}, target_harness="opencode")
    assert plan["apply_allowed"]
    assert plan["memory"]["already_present"] == 1
    assert plan["capabilities"]["missing"] == ["ask_user", "telepathy"]


def test_reconcile_surfaces_version_conflict(monkeypatch):
    monkeypatch.setenv(cc.HMAC_ENV, "test-key")
    capsule = _capsule(memories=[{"memory_id": "mem_x", "content_hash": "remote"}])
    plan = cc.reconcile(capsule, {"mem_x": "local"}, target_harness="codex")
    assert plan["memory"]["conflicts"] == ["mem_x"]


def test_unknown_harness_cannot_apply(monkeypatch):
    monkeypatch.setenv(cc.HMAC_ENV, "test-key")
    plan = cc.reconcile(_capsule(), set(), target_harness="unknown-body")
    assert not plan["apply_allowed"]
    assert not plan["harness_known"]


def test_transfer_health_exposes_dimensions(monkeypatch):
    monkeypatch.setenv(cc.HMAC_ENV, "test-key")
    health = cc.transfer_health(_capsule(), "codex")
    assert health["score"] == 100
    assert health["status"] == "full"
    assert set(health["dimensions"]) == {
        "integrity", "identity", "governance", "memory", "skills", "harness",
        "tool_coverage", "lifecycle_coverage"}


def test_opencode_reports_full_lifecycle_with_semantic_limitations(monkeypatch):
    monkeypatch.setenv(cc.HMAC_ENV, "test-key")
    health = cc.transfer_health(_capsule(), "opencode")
    profile = cc.load_harness_profiles()["opencode"]
    assert health["status"] == "full"
    assert health["dimensions"]["lifecycle_coverage"] == 1.0
    assert "ask_user" not in profile["semantic_capabilities"]
    assert "pre-tool supports blocking but not advisory context" in profile["limitations"]


def test_skill_projects_to_every_harness(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    rendered = "---\nname: mind-probe\ndescription: probe\n---\n\nworks\n"
    outputs = skill_projection.project_skill("mind-probe", rendered)
    assert set(outputs) == {"canonical", "claude-code", "codex", "opencode"}
    for path in outputs.values():
        assert open(path, encoding="utf-8").read() == rendered


def test_capsule_digest_is_deterministic():
    capsule = _capsule()
    clone = copy.deepcopy(capsule)
    del clone["integrity"]
    assert cc.seal(clone, signing_key="test-key")["integrity"] == capsule["integrity"]
