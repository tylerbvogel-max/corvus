"""The delivery axis (mind-charter-composition) — hermetic tests.

Covers: verdict parsing and its fail-closed default, the separation of
delivery from authority, charter selection on the delivery axis, the
refusal to ship an empty charter when nothing has been judged, manifest
preservation across a skipped compile, and re-audit eligibility.
No DB, no LLM, no network.
"""

import pytest

from app.services import delivery_mode as dm
from app.services.delivery_mode import RETRIEVABLE, STANDING, _parse


# ── verdict parsing ──────────────────────────────────────────────────

def test_parses_well_formed_verdicts():
    text = ('[{"item": 1, "verdict": "standing", "reason": "a rule"},'
            ' {"item": 2, "verdict": "retrievable", "reason": "a path"}]')
    assert _parse(text, 2) == {1: (STANDING, "a rule"), 2: (RETRIEVABLE, "a path")}


def test_parses_around_prose_and_fences():
    text = ('Sure!\n```json\n[{"item": 1, "verdict": "standing", "reason": "r"}]'
            '\n```\nDone.')
    assert _parse(text, 1) == {1: (STANDING, "r")}


@pytest.mark.parametrize("text", [
    "not json at all",
    '[{"item": 1, "verdict": "maybe", "reason": "r"}]',
    '[{"item": 9, "verdict": "standing", "reason": "r"}]',
    '[{"verdict": "standing"}]',
    "",
])
def test_unusable_verdicts_leave_the_neuron_unjudged(text):
    """A parse failure must never default to standing — that would let a
    broken judge call buy permanent injection."""
    assert _parse(text, 2) == {}


def test_partial_batch_keeps_the_good_verdicts():
    text = ('[{"item": 1, "verdict": "standing", "reason": "r"},'
            ' {"item": 2, "verdict": "nonsense", "reason": "r"}]')
    parsed = _parse(text, 2)
    assert parsed == {1: (STANDING, "r")}


def test_reason_is_bounded():
    text = '[{"item": 1, "verdict": "standing", "reason": "%s"}]' % ("x" * 900)
    assert len(_parse(text, 1)[1][1]) == 400


# ── delivery is independent of authority ─────────────────────────────

@pytest.mark.asyncio
async def test_classification_never_touches_authority_or_activity(monkeypatch):
    """Demotion out of the charter is a DELIVERY change. The neuron keeps
    its earned trust and stays fully retrievable."""
    neuron = _FakeNeuron(1, "Corvus backend venv at backend/venv")
    neuron.authority_level, neuron.avg_utility = "guidance", 0.95

    async def fake_judge(batch):
        return {1: (RETRIEVABLE, "a path, answerable on demand")}

    monkeypatch.setattr(dm, "_judge", fake_judge)
    monkeypatch.setattr(dm, "_log_action", lambda a, d: None)
    monkeypatch.setattr(dm, "pending_classification",
                        _async_return([neuron]))
    report = await dm.classify_delivery(_FakeDb())

    assert neuron.delivery_mode == RETRIEVABLE
    assert neuron.authority_level == "guidance", "authority must be untouched"
    assert neuron.avg_utility == 0.95, "trust must be untouched"
    assert neuron.is_active is True, "demotion is not retirement"
    assert report[RETRIEVABLE] == 1 and report["judged"] == 1


@pytest.mark.asyncio
async def test_verdict_changes_are_reported_for_receipts(monkeypatch):
    was_standing = _FakeNeuron(1, "Corvus-mind runs on port 8005")
    was_standing.delivery_mode = STANDING
    unchanged = _FakeNeuron(2, "Never push this repo to the public remote")
    unchanged.delivery_mode = STANDING

    async def fake_judge(batch):
        return {1: (RETRIEVABLE, "a port number"), 2: (STANDING, "a rule")}

    monkeypatch.setattr(dm, "_judge", fake_judge)
    monkeypatch.setattr(dm, "_log_action", lambda a, d: None)
    monkeypatch.setattr(dm, "pending_classification",
                        _async_return([was_standing, unchanged]))
    report = await dm.classify_delivery(_FakeDb())

    assert [c["neuron_id"] for c in report["changes"]] == [1]
    assert report["changes"][0]["from"] == STANDING
    assert report["changes"][0]["to"] == RETRIEVABLE


@pytest.mark.asyncio
async def test_unjudged_items_are_counted_not_assumed(monkeypatch):
    a, b = _FakeNeuron(1, "one"), _FakeNeuron(2, "two")

    async def fake_judge(batch):
        return {1: (STANDING, "r")}   # item 2 missing from the reply

    monkeypatch.setattr(dm, "_judge", fake_judge)
    monkeypatch.setattr(dm, "_log_action", lambda a, d: None)
    monkeypatch.setattr(dm, "pending_classification", _async_return([a, b]))
    report = await dm.classify_delivery(_FakeDb())

    assert report["unjudged"] == 1
    assert b.delivery_mode is None, "an unjudged neuron stays unclassified"


# ── charter selection now rides the delivery axis ────────────────────

def test_eligibility_keeps_every_wall_and_owns_no_delivery_opinion():
    """charter_eligible_filters is the trust/identity/scope gate ONLY.
    Guardrail regression: the identity wall (reference-class exclusion)
    and the Assistant-scope exclusion must survive, and delivery must NOT
    be baked in here or the classifier would only ever re-judge neurons
    that already passed it."""
    from sqlalchemy import select as sa_select

    from app.models import Neuron
    from app.services.delivery_mode import charter_eligible_filters

    clause = str(sa_select(Neuron.id).where(*charter_eligible_filters()))
    assert "neurons.department != " in clause, "Assistant exclusion missing"
    assert "neurons.source_origin != " in clause, "identity wall missing"
    assert "neurons.superseded_by IS NULL" in clause
    assert "neurons.authority_level IN" in clause
    assert "delivery_mode" not in clause, "eligibility must not judge delivery"


@pytest.mark.asyncio
async def test_charter_renders_only_standing_neurons(tmp_path, monkeypatch):
    from app.services import skill_compiler

    monkeypatch.setattr(skill_compiler, "_log_action", lambda a, d: None)
    written = {}
    monkeypatch.setattr(skill_compiler, "_write_skill",
                        lambda name, desc, body, sources:
                        _record(written, tmp_path, name, body, sources))
    policy = _FakeNeuron(1, "Never push this repo to the public remote")
    policy.delivery_mode = STANDING
    result = await skill_compiler.compile_charter(_QueryDb([[policy]]))

    assert result["included"] == 1
    assert result["sources"] == [1]
    assert "Never push this repo" in written["body"]


@pytest.mark.asyncio
async def test_unclassified_corpus_never_ships_an_empty_charter(monkeypatch):
    """If nothing has been judged, that is a missing classifier run — not
    evidence that no policy exists. Blanking the capsule would silently
    strip standing context from every future session."""
    from app.services import skill_compiler

    logged = []
    monkeypatch.setattr(skill_compiler, "_log_action",
                        lambda a, d: logged.append(a))
    monkeypatch.setattr(skill_compiler, "_write_skill", _must_not_write)
    result = await skill_compiler.compile_charter(_QueryDb([[], 0]))

    assert result["skipped"] == "no delivery verdicts yet"
    assert result["path"] is None
    assert "compiler.charter_unclassified" in logged


@pytest.mark.asyncio
async def test_all_retrievable_is_a_real_verdict_not_a_skip(monkeypatch):
    """Judged-but-none-standing is a genuine result, so it must NOT hit
    the unclassified safety path."""
    from app.services import skill_compiler

    monkeypatch.setattr(skill_compiler, "_log_action", lambda a, d: None)
    monkeypatch.setattr(skill_compiler, "_write_skill", _must_not_write)
    result = await skill_compiler.compile_charter(_QueryDb([[], 12]))

    assert "skipped" not in result
    assert result["included"] == 0


def test_skipped_compile_preserves_the_existing_manifest_entry():
    """Dropping the entry would strip the capsule's source ids and kill
    charter attribution while the capsule kept injecting from disk."""
    from app.services.skill_compiler import CHARTER_NAME, _apply_charter_entry

    manifest = [{"name": CHARTER_NAME, "sources": [1, 2], "designated": True},
                {"name": "mind-other", "sources": [3]}]
    kept = _apply_charter_entry(manifest, {"skipped": "no delivery verdicts yet"})
    assert kept == manifest


def test_successful_compile_replaces_the_entry():
    from app.services.skill_compiler import CHARTER_NAME, _apply_charter_entry

    manifest = [{"name": CHARTER_NAME, "sources": [1, 2], "designated": True}]
    out = _apply_charter_entry(manifest, {
        "path": "/tmp/x", "sources": [9], "source_labels": ["policy"]})
    entries = [m for m in out if m["name"] == CHARTER_NAME]
    assert len(entries) == 1 and entries[0]["sources"] == [9]


# ── fakes ────────────────────────────────────────────────────────────

def _record(store, tmp_path, name, body, sources):
    store.update({"name": name, "body": body, "sources": sources})
    path = tmp_path / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _must_not_write(*args, **kwargs):
    raise AssertionError("no charter may be written on this path")


class _QueryResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value

    def scalar(self):
        return self._value


class _QueryDb:
    """Returns queued results in call order."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return _QueryResult(self._results.pop(0))

    async def commit(self):
        return None


class _FakeNeuron:
    def __init__(self, nid, label):
        self.id, self.label = nid, label
        self.summary = self.content = label
        self.department = "Projects"
        self.authority_level = "guidance"
        self.avg_utility = 0.7
        self.is_active = True
        self.delivery_mode = None
        self.delivery_reason = None
        self.delivery_judged_at = None
        self.last_verified = None


class _FakeDb:
    async def commit(self):
        return None


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner
