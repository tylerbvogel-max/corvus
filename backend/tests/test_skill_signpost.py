"""Skill signposting (mind-skill-signpost) — hermetic tests.

Covers: member-lesson vote threshold, the direct skill-node-score path
(mind-skill-node-scoring), pointer cap + ordering, designated
(charter/self-model) exclusion, missing-rendering exclusion, stale
skill-node mapping, frontmatter description parsing, the scaffolding
filter, and the no-LLM guarantee for the recall hot path. Also
exercises the injection hook's pointer rendering and session dedupe.
No DB, no LLM, no network.
"""

import ast
import importlib.util
import inspect
import json
import os

import pytest

from app.services import skill_signpost
from app.services.skill_signpost import skill_pointers_for


def _write_skill(tmp_path, name, description="Use when testing."):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
                 encoding="utf-8")
    return str(p)


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    """Write entries + renderings, point the module at the temp manifest."""
    path = tmp_path / "compiled-skills.json"
    monkeypatch.setattr(skill_signpost, "MANIFEST_PATH", str(path))

    def _write(entries):
        path.write_text(json.dumps(entries), encoding="utf-8")

    return _write


def _entry(tmp_path, name, sources, **extra):
    return {"name": name, "sources": sources,
            "path": _write_skill(tmp_path, name), **extra}


# ── Eligibility: member-lesson vote ─────────────────────────────────

def test_vote_threshold_requires_two_members(manifest, tmp_path):
    manifest([_entry(tmp_path, "mind-a", [1, 2, 3])])
    assert skill_pointers_for([(1, 0.9)]) == []          # 1 vote: below
    got = skill_pointers_for([(1, 0.9), (2, 0.8)])       # 2 votes: eligible
    assert [p["name"] for p in got] == ["mind-a"]
    assert got[0]["votes"] == 2
    assert got[0]["top_member_score"] == 0.9
    assert got[0]["description"] == "Use when testing."
    assert got[0]["path"] == "member-vote"


def test_nonmember_candidates_do_not_vote(manifest, tmp_path):
    manifest([_entry(tmp_path, "mind-a", [1, 2])])
    assert skill_pointers_for([(1, 0.9), (99, 0.9), (98, 0.9)]) == []


def test_cap_two_sorted_by_votes_then_score(manifest, tmp_path):
    manifest([
        _entry(tmp_path, "mind-low", [1, 2]),
        _entry(tmp_path, "mind-high", [1, 2, 3]),
        _entry(tmp_path, "mind-mid", [4, 5]),
    ])
    got = skill_pointers_for(
        [(1, 0.5), (2, 0.6), (3, 0.7), (4, 0.95), (5, 0.4)])
    # mind-high wins on votes (3); mind-mid beats mind-low on top score.
    assert [p["name"] for p in got] == ["mind-high", "mind-mid"]
    assert len(got) == skill_signpost.MAX_POINTERS == 2


# ── Direct path: skill-node score (mind-skill-node-scoring) ─────────

def test_direct_path_fires_at_threshold(manifest, tmp_path):
    """The #1137 shape: member vote whiffs, the skill's own node scores."""
    manifest([_entry(tmp_path, "mind-a", [1, 2, 3])])
    thr = skill_signpost.SKILL_NODE_MIN_SCORE
    got = skill_pointers_for([(1, 0.9)], [("mind-a", thr)])
    assert [p["name"] for p in got] == ["mind-a"]
    assert got[0]["path"] == "node-score"
    assert got[0]["votes"] == 1
    assert got[0]["node_score"] == thr


def test_direct_path_below_threshold_stays_dark(manifest, tmp_path):
    manifest([_entry(tmp_path, "mind-a", [1, 2, 3])])
    thr = skill_signpost.SKILL_NODE_MIN_SCORE
    assert skill_pointers_for([], [("mind-a", thr - 0.01)]) == []


def test_stale_skill_nodes_never_map(manifest, tmp_path):
    """The graph carries ACTIVE skill nodes with no live manifest entry
    (#186 mind-read-before-edit-tool, #230 mind-corvus-dev-servers) —
    a hot stale node must not signpost anything."""
    manifest([_entry(tmp_path, "mind-a", [1, 2])])
    assert skill_pointers_for([], [("mind-read-before-edit-tool", 0.99)]) == []


def test_direct_path_respects_missing_rendering(manifest, tmp_path):
    entry = _entry(tmp_path, "mind-gone", [1, 2])
    os.unlink(entry["path"])
    manifest([entry])
    assert skill_pointers_for([], [("mind-gone", 0.99)]) == []


def test_direct_path_respects_designated(manifest, tmp_path):
    manifest([_entry(tmp_path, "mind-charter", [1, 2], designated=True)])
    assert skill_pointers_for([], [("mind-charter", 0.99)]) == []


def test_both_paths_dedupe_to_one_pointer(manifest, tmp_path):
    manifest([_entry(tmp_path, "mind-a", [1, 2])])
    got = skill_pointers_for([(1, 0.9), (2, 0.8)], [("mind-a", 0.95)])
    assert len(got) == 1
    assert got[0]["path"] == "both"
    assert got[0]["votes"] == 2
    assert got[0]["node_score"] == 0.95


def test_member_win_outranks_lone_hot_node(manifest, tmp_path):
    """Sort must handle vote-less direct pointers: a 2-vote member win
    beats a lone hot node even when the node's score is higher."""
    manifest([
        _entry(tmp_path, "mind-node-only", [7, 8, 9]),
        _entry(tmp_path, "mind-voted", [1, 2]),
    ])
    got = skill_pointers_for(
        [(1, 0.5), (2, 0.4)], [("mind-node-only", 0.99)])
    assert [p["name"] for p in got] == ["mind-voted", "mind-node-only"]
    assert got[1]["votes"] == 0
    assert got[1]["top_member_score"] == 0.0


# ── Slot reclamation: skill nodes are pointer fuel, not hits ────────

def test_skill_is_scaffolding_node_type():
    from app.routers.recall import _SCAFFOLDING_NODE_TYPES
    assert "skill" in _SCAFFOLDING_NODE_TYPES


# ── Exclusions ───────────────────────────────────────────────────────

def test_designated_capsules_never_signposted(manifest, tmp_path):
    manifest([
        _entry(tmp_path, "mind-charter", [1, 2], designated=True),
        _entry(tmp_path, "mind-self-model", [1, 2], designated=True),
    ])
    assert skill_pointers_for([(1, 0.9), (2, 0.9)]) == []


def test_retired_flag_excluded(manifest, tmp_path):
    manifest([_entry(tmp_path, "mind-old", [1, 2], retired=True)])
    assert skill_pointers_for([(1, 0.9), (2, 0.9)]) == []


def test_missing_rendering_excluded(manifest, tmp_path):
    entry = _entry(tmp_path, "mind-gone", [1, 2])
    os.unlink(entry["path"])  # retired out-of-band: manifest row is stale
    manifest([entry])
    assert skill_pointers_for([(1, 0.9), (2, 0.9)]) == []


def test_missing_manifest_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_signpost, "MANIFEST_PATH",
                        str(tmp_path / "nope.json"))
    assert skill_pointers_for([(1, 0.9), (2, 0.9)]) == []


# ── Frontmatter parsing ──────────────────────────────────────────────

def test_description_missing_still_emits_pointer(manifest, tmp_path):
    entry = _entry(tmp_path, "mind-bare", [1, 2])
    with open(entry["path"], "w", encoding="utf-8") as fh:
        fh.write("# no frontmatter at all\n")
    manifest([entry])
    got = skill_pointers_for([(1, 0.9), (2, 0.8)])
    assert [p["name"] for p in got] == ["mind-bare"]
    assert got[0]["description"] == ""


# ── No-LLM guarantee ─────────────────────────────────────────────────

def test_signpost_module_is_llm_free():
    """The recall hot path must stay LLM-free: the signpost module may
    import nothing that can reach a model or the network."""
    tree = ast.parse(inspect.getsource(skill_signpost))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"json", "app"}
    forbidden = {"subprocess", "urllib", "httpx", "requests", "anthropic"}
    assert not imported & forbidden


# ── Injection hook rendering + session dedupe ────────────────────────

def _load_hook():
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "harness", "claude-code", "memory_inject_hook.py")
    spec = importlib.util.spec_from_file_location("memory_inject_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_pointer_line_format():
    hook = _load_hook()
    lines = hook._pointer_lines([
        {"name": "mind-corvus-backend-services",
         "description": "Use when editing services.", "votes": 3},
        {"name": "mind-bare", "description": "", "votes": 2},
    ])
    assert lines[0] == ("Relevant playbook: mind-corvus-backend-services — "
                        "Use when editing services. "
                        "(load the skill for the full procedure)")
    assert lines[1] == ("Relevant playbook: mind-bare "
                        "(load the skill for the full procedure)")


def test_hook_session_dedupe_roundtrip(tmp_path, monkeypatch):
    hook = _load_hook()
    monkeypatch.setattr(hook, "EPISODE_DIR", str(tmp_path))
    pointers = [{"name": "mind-a", "votes": 2}]
    assert hook._already_pointed("s1") == set()
    hook._log_pointers("s1", "/tmp", "UserPromptSubmit", pointers, query_id=7)
    assert hook._already_pointed("s1") == {"mind-a"}
    rec = json.loads((tmp_path / "s1.jsonl").read_text().splitlines()[0])
    assert rec["event"] == "SkillPointer"
    assert rec["query_id"] == 7
    assert rec["skills"] == [{"name": "mind-a", "votes": 2}]


def test_hook_log_carries_path_and_node_score(tmp_path, monkeypatch):
    """Which eligibility path earned the pointer rides into the episode
    log — the conversion instrument learns which path earns pulls.
    Vote-less direct pointers keep votes=0 (0 is not absent)."""
    hook = _load_hook()
    monkeypatch.setattr(hook, "EPISODE_DIR", str(tmp_path))
    hook._log_pointers("s2", "/tmp", "UserPromptSubmit", [
        {"name": "mind-a", "votes": 0, "path": "node-score",
         "node_score": 0.77, "description": "x"},
    ], query_id=9)
    rec = json.loads((tmp_path / "s2.jsonl").read_text().splitlines()[0])
    assert rec["skills"] == [{"name": "mind-a", "votes": 0,
                              "path": "node-score", "node_score": 0.77}]
