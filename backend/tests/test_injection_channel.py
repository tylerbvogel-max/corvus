"""Delivery-channel split for injection (mind-charter-composition) —
hermetic tests.

Covers: trigger classification, the marker-timestamp join used to
recover history (including the second-truncation window that makes a
marker stamp before the lines it followed), the neuron-membership
validation that rejects a wrong join, one-verdict-per-neuron-per-session
counting on BOTH sides of the rate, refusal to guess when triggers
disagree, and preference for a stamped channel over reconstruction.
No DB, no LLM, no network.
"""

import json

import pytest

from app.services import injection_channel as ic
from app.services.injection_channel import (
    RETRIEVED, STANDING, channel_for_trigger, reconstruct_history,
    standing_volume,
)


# ── channel classification ───────────────────────────────────────────

@pytest.mark.parametrize("trigger,expected", [
    ("SessionStart", STANDING),
    ("capsule:mind-charter", STANDING),
    ("capsule:mind-self-model", STANDING),
    ("capsule:anything-future", STANDING),
    ("UserPromptSubmit", RETRIEVED),
    ("PreToolUse", RETRIEVED),
    ("unknown", RETRIEVED),
])
def test_channel_for_trigger(trigger, expected):
    """SessionStart is standing despite running a recall query: the query
    is generic and fires before the session has a subject."""
    assert channel_for_trigger(trigger) == expected


# ── corpus fixture ───────────────────────────────────────────────────

def _session(tmp_path, sid, injections, distilled_at=None):
    """injections: [(trigger, [neuron_id, ...])] or [(trigger, ids, tool)]

    The three-tuple form writes the `tool` field the hook started stamping on
    PreToolUse injections in 2026-08; the two-tuple form is the legacy record
    shape, deliberately kept so every test above still exercises it.
    """
    path = tmp_path / f"{sid}.jsonl"
    lines = []
    for entry in injections:
        trigger, ids = entry[0], entry[1]
        tool = entry[2] if len(entry) > 2 else None
        record = {
            "event": "Injection", "session_id": sid, "trigger": trigger,
            "neuron_ids": ids, "labels": [f"lesson {n}" for n in ids]}
        if tool:
            record["tool"] = tool
        lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if distilled_at:
        (tmp_path / f"{sid}.jsonl.distilled").write_text(
            json.dumps({"distilled_at": distilled_at}), encoding="utf-8")
    return path


def _actions(tmp_path, rows):
    path = tmp_path / "janitor-actions.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "EPISODE_DIR", str(tmp_path))
    monkeypatch.setattr(ic, "ACTIONS_LOG", str(tmp_path / "janitor-actions.jsonl"))
    return tmp_path


# ── historical reconstruction ────────────────────────────────────────

def test_split_separates_the_two_channels(corpus):
    """A reward on a capsule neuron and a reward on a PreToolUse neuron
    must land in different channels, each with its own denominator."""
    _session(corpus, "s1", [("capsule:mind-charter", [1, 2, 3]),
                            ("PreToolUse", [7])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 1},
        {"ts": "2026-07-20T10:00:00.200+00:00", "action": "attribution.reward",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["by_channel"][STANDING] == {
        "injected": 3, "reward": 1, "penalty": 0,
        "load_bearing_pct": pytest.approx(33.33, abs=0.01)}
    assert report["by_channel"][RETRIEVED] == {
        "injected": 1, "reward": 1, "penalty": 0, "load_bearing_pct": 100.0}
    assert report["unresolved_lines"] == 0


def test_marker_may_be_stamped_before_its_own_attribution_lines(corpus):
    """distilled_at is second-truncated, so the marker can read up to a
    second earlier than lines written during the same pass. A one-sided
    join loses those; this is the bug that first made the split read 10%
    resolvable instead of 99.7%."""
    _session(corpus, "s1", [("PreToolUse", [7])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.900+00:00", "action": "attribution.reward",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["unresolved_lines"] == 0
    assert report["by_channel"][RETRIEVED]["reward"] == 1


def test_join_requires_the_session_to_have_injected_that_neuron(corpus):
    """Timestamp proximity alone is not evidence. A neuron the session
    never received must not be credited to it."""
    _session(corpus, "s1", [("PreToolUse", [7])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 999},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["unresolved_lines"] == 1
    assert report["by_channel"][RETRIEVED]["reward"] == 0


def test_disagreeing_candidates_are_unresolved_not_guessed(corpus):
    """Two sessions distilled in the same instant both hold the neuron,
    on opposite channels. That line is reported, never assigned."""
    _session(corpus, "s1", [("capsule:mind-charter", [5])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _session(corpus, "s2", [("PreToolUse", [5])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 5},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["unresolved_lines"] == 1
    assert report["by_channel"][STANDING]["reward"] == 0
    assert report["by_channel"][RETRIEVED]["reward"] == 0


def test_repeat_delivery_is_one_attribution_unit(corpus):
    """Attribution renders one verdict per neuron per session, so the
    denominator must count the neuron once however often it arrived —
    otherwise the rate is deflated by delivery volume."""
    _session(corpus, "s1", [("SessionStart", [4]),
                            ("capsule:mind-charter", [4])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["by_channel"][STANDING]["injected"] == 1
    # Both deliveries are standing, so the channel is certain even though
    # the trigger is not.
    assert report["cross_channel_neurons"] == 0
    assert report["ambiguous_trigger_neurons"] == 1
    assert report["by_trigger"] == {}


def test_undistilled_sessions_are_excluded_from_denominators(corpus):
    """A session with no verdicts contributes no numerator; counting its
    injections would understate every rate."""
    _session(corpus, "s1", [("PreToolUse", [7])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _session(corpus, "s2", [("PreToolUse", [8, 9])])  # never distilled
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["sessions_scanned"] == 2
    assert report["sessions_distilled"] == 1
    assert report["by_channel"][RETRIEVED]["injected"] == 1


def test_stamped_channel_is_preferred_over_reconstruction(corpus):
    """Forward path: once the distiller records the channel, the rate no
    longer depends on the timestamp join at all."""
    _session(corpus, "s1", [("capsule:mind-charter", [1])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        # No session could match this timestamp, yet it still resolves.
        {"ts": "2001-01-01T00:00:00+00:00", "action": "attribution.reward",
         "neuron_id": 1, "channel": STANDING, "trigger": "capsule:mind-charter"},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["stamped_lines"] == 1
    assert report["reconstructed_lines"] == 0
    assert report["unresolved_lines"] == 0
    assert report["by_channel"][STANDING]["reward"] == 1


def test_penalties_do_not_count_as_load_bearing(corpus):
    _session(corpus, "s1", [("PreToolUse", [7, 8])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.penalty",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["by_channel"][RETRIEVED]["penalty"] == 1
    assert report["by_channel"][RETRIEVED]["load_bearing_pct"] == 0.0


def test_empty_corpus_reports_none_not_zero(corpus):
    """No data must not masquerade as a measured zero."""
    _actions(corpus, [])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["pooled"]["load_bearing_pct"] is None
    assert report["standing_share_pct"] is None


# ── PreToolUse tool sub-split (mind-pretooluse-reach) ────────────────

def test_legacy_records_without_a_tool_field_read_as_bash(corpus):
    """Every PreToolUse injection ever logged before the `tool` field was
    added came through a gate that admitted Bash and nothing else, so
    reading absence as Bash is a fact about the writer, not a default.
    Without this the widening would orphan 1,900+ historical injections and
    the before/after would compare two different rows."""
    _session(corpus, "s1", [("PreToolUse", [7, 8])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["pretooluse_by_tool"] == {
        "Bash": {"injected": 2, "reward": 1, "penalty": 0,
                 "load_bearing_pct": 50.0}}


def test_bash_sub_rate_survives_a_widened_gate(corpus):
    """The failure this split exists to prevent: Edit/Write injections
    that earn nothing must not be able to dilute a healthy Bash rate into
    a pooled number that still looks fine."""
    _session(corpus, "s1", [("PreToolUse", [7, 8], "Bash"),
                            ("PreToolUse", [20, 21, 22, 23], "Edit")],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    # Pooled would read 1/6 = 16.7% and hide which half earned it.
    assert report["by_trigger"]["PreToolUse"]["load_bearing_pct"] == pytest.approx(
        16.67, abs=0.01)
    assert report["pretooluse_by_tool"]["Bash"] == {
        "injected": 2, "reward": 1, "penalty": 0, "load_bearing_pct": 50.0}
    assert report["pretooluse_by_tool"]["Edit"] == {
        "injected": 4, "reward": 0, "penalty": 0, "load_bearing_pct": 0.0}


def test_tool_sub_split_sums_to_the_trigger_row_it_splits(corpus):
    """A sub-rate that does not reconcile with its parent is a second
    number, not a breakdown."""
    _session(corpus, "s1", [("PreToolUse", [7, 8], "Bash"),
                            ("PreToolUse", [20], "Write")],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 8},
        {"ts": "2026-07-20T10:00:00.200+00:00", "action": "attribution.penalty",
         "neuron_id": 20},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    parent = report["by_trigger"]["PreToolUse"]
    for key in ("injected", "reward", "penalty"):
        assert parent[key] == sum(
            v[key] for v in report["pretooluse_by_tool"].values()), key


def test_neuron_delivered_by_two_tools_is_not_credited_to_either(corpus):
    """Same refusal-to-guess rule the trigger split already uses: one
    verdict per neuron per session cannot be owned by two tools."""
    _session(corpus, "s1", [("PreToolUse", [7], "Bash"),
                            ("PreToolUse", [7], "Edit")],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [
        {"ts": "2026-07-20T10:00:00.100+00:00", "action": "attribution.reward",
         "neuron_id": 7},
    ])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert report["ambiguous_tool_units"] >= 1
    assert report["pretooluse_by_tool"] == {}
    # The trigger row is unaffected — the neuron still arrived via PreToolUse.
    assert report["by_trigger"]["PreToolUse"]["injected"] == 1


def test_other_triggers_are_absent_from_the_tool_split(corpus):
    """Only PreToolUse is gated by tool; nothing else may appear here."""
    _session(corpus, "s1", [("UserPromptSubmit", [1]),
                            ("capsule:mind-charter", [2]),
                            ("PreToolUse", [7], "Bash")],
             distilled_at="2026-07-20T10:00:00+00:00")
    _actions(corpus, [])
    report = reconstruct_history(str(corpus), str(corpus / "janitor-actions.jsonl"))
    assert set(report["pretooluse_by_tool"]) == {"Bash"}


# ── volume ───────────────────────────────────────────────────────────

def test_standing_volume_counts_all_sessions(corpus):
    """Delivery volume does not depend on distillation, so the
    before/after baseline spans undistilled sessions too."""
    _session(corpus, "s1", [("capsule:mind-charter", [1, 2]),
                            ("PreToolUse", [7])],
             distilled_at="2026-07-20T10:00:00+00:00")
    _session(corpus, "s2", [("capsule:mind-charter", [1, 2, 3])])
    volume = standing_volume(str(corpus))
    assert volume["sessions"] == 2
    assert volume["standing_total"] == 5
    assert volume["retrieved_total"] == 1
    assert volume["standing_per_session"] == 2.5


# ── forward stamping (distiller writes the channel, nothing infers it) ─

def test_load_log_stamps_channel_on_every_injection(corpus):
    from app.services.distiller import _load_log

    path = _session(corpus, "s1", [("capsule:mind-charter", [1]),
                                   ("PreToolUse", [7])])
    _events, injections, _transcript = _load_log(str(path))
    stamped = {i["label"]: (i["trigger"], i["channel"]) for i in injections}
    assert stamped["lesson 1"] == ("capsule:mind-charter", STANDING)
    assert stamped["lesson 7"] == ("PreToolUse", RETRIEVED)


class _Neuron:
    def __init__(self, nid):
        self.id, self.label = nid, f"lesson {nid}"
        self.is_active, self.avg_utility = True, 0.5


class _Db:
    """Minimal stand-in: attribution only gets and adds."""

    def __init__(self, neurons):
        self._neurons = {n.id: n for n in neurons}
        self.added = []

    async def get(self, _model, nid):
        return self._neurons.get(nid)

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.asyncio
async def test_attribution_tallies_verdicts_per_channel(monkeypatch, tmp_path):
    """A charter reward and a tool-warning reward must be separable at
    the moment the verdict is applied, not reconstructed later."""
    from app.services import distiller, mind_janitors

    logged = []
    monkeypatch.setattr(mind_janitors, "_log_action",
                        lambda action, payload: logged.append((action, payload)))
    injections = [
        {"label": "lesson 1", "neuron_id": 1, "query_id": None,
         "trigger": "capsule:mind-charter", "channel": STANDING},
        {"label": "lesson 7", "neuron_id": 7, "query_id": 55,
         "trigger": "PreToolUse", "channel": RETRIEVED},
        {"label": "lesson 8", "neuron_id": 8, "query_id": 56,
         "trigger": "PreToolUse", "channel": RETRIEVED},
    ]
    verdicts = [
        {"label": "lesson 1", "verdict": "load_bearing", "evidence": "e"},
        {"label": "lesson 7", "verdict": "contradicted", "evidence": "e"},
        {"label": "lesson 8", "verdict": "unused", "evidence": "e"},
    ]
    db = _Db([_Neuron(1), _Neuron(7), _Neuron(8)])
    counts = await distiller._apply_attributions(db, verdicts, injections)

    assert counts["rewarded"] == 1 and counts["penalized"] == 1
    assert counts["by_channel"][STANDING] == {
        "rewarded": 1, "penalized": 0, "unused": 0}
    assert counts["by_channel"][RETRIEVED] == {
        "rewarded": 0, "penalized": 1, "unused": 1}
    # Provenance is on the log line too, so the split reads without a join.
    by_neuron = {p["neuron_id"]: p for _a, p in logged}
    assert by_neuron[1]["channel"] == STANDING
    assert by_neuron[1]["trigger"] == "capsule:mind-charter"
    assert by_neuron[7]["channel"] == RETRIEVED


@pytest.mark.asyncio
async def test_label_delivered_by_both_channels_is_not_credited_to_one(
        monkeypatch, tmp_path):
    """One verdict covers every delivery of a label, so a label that
    crossed channels has no honest single owner."""
    from app.services import distiller, mind_janitors

    monkeypatch.setattr(mind_janitors, "_log_action", lambda a, p: None)
    injections = [
        {"label": "lesson 1", "neuron_id": 1, "query_id": None,
         "trigger": "capsule:mind-charter", "channel": STANDING},
        {"label": "lesson 1", "neuron_id": 1, "query_id": 55,
         "trigger": "PreToolUse", "channel": RETRIEVED},
    ]
    verdicts = [{"label": "lesson 1", "verdict": "load_bearing", "evidence": "e"}]
    counts = await distiller._apply_attributions(
        _Db([_Neuron(1)]), verdicts, injections)
    assert counts["rewarded"] == 1
    assert set(counts["by_channel"]) == {"ambiguous"}
