"""Cheap rot guard for the EXPENSIVE kernel replay (kernel-replay-ungated).

tests/replay_nvm_throwaway.py is the only artifact that exercises the
reconsolidation kernel end to end against a real database, the real
one-step lifecycle and the real action bus. It needs a throwaway Postgres,
so it cannot join the hermetic lane, and on 2026-08-01 it was found to have
been failing since evidence-frame enforcement landed — invisibly, because
no merge-gate lane runs it.

THE BARGAIN THIS FILE STRIKES: the expensive proof may stay manual, but its
INPUTS are guarded by a test that runs on every PR in milliseconds and needs
no database. Every check below would have caught the original rot.

What this file does NOT claim: it does not prove the kernel works. It proves
the replay would still be MEANINGFUL if run — that its packets are
admissible and still imply the outcomes it asserts. Kernel behaviour against
real SQL remains the replay's job; see the `kernel-replay` lane in
scripts/run_test_lane.py.
"""

from __future__ import annotations

import pytest

from app.services.reconsolidation.plan import Disposition
from app.services.reconsolidation.review import (
    compute_coverage_delta, decide_disposition, parse_facets, validate_packet,
)
from tests.golden_frames import GOLDEN_NVM_CONTENT, TWO_MEMBER_NVM_CONTENT
from tests.kernel_matrix_fixtures import (
    MATRIX_CASES, STALE_PLAINTEXT_MEMBERS, STALE_PLAINTEXT_PACKET, build_neuron,
)

pytestmark = pytest.mark.hermetic

_CASE_IDS = [c.key for c in MATRIX_CASES]


@pytest.mark.parametrize("case", MATRIX_CASES, ids=_CASE_IDS)
def test_matrix_packet_is_still_admissible(case):
    """THE regression: every replay packet must still pass validate_packet.

    This is the exact check whose absence let the replay rot. If evidence
    frames, fingerprints, scopes or facet rules tighten again, this fails in
    milliseconds with the real violation text instead of surfacing weeks
    later as `KeyError: 'disposition'` from a database run nobody makes.
    """
    members = [build_neuron(m) for m in case.members]
    violations = validate_packet(case.packet, members)
    assert violations == [], (
        f"replay fixture {case.key!r} is stale — the kernel is not "
        f"implicated. validate_packet says: {violations}"
    )


@pytest.mark.parametrize("case", MATRIX_CASES, ids=_CASE_IDS)
def test_matrix_case_still_implies_its_asserted_outcome(case):
    """A valid packet is not enough: it must still MEAN what the replay
    asserts about it. Disposition is pure, so drift is catchable here."""
    members = [build_neuron(m) for m in case.members]
    facets = parse_facets(case.packet)
    disposition, canonical_id = decide_disposition(
        members, facets, compute_coverage_delta(facets),
        case.packet.get("proposed_scope"),
    )
    assert disposition.value == case.expectation, (
        f"{case.key!r} now decides {disposition.value!r}, but the replay "
        f"asserts {case.expectation!r} — {case.detail}"
    )
    assert canonical_id == case.expected_canonical_id


def test_stale_plaintext_packet_is_refused():
    """The rot detector must BITE.

    This is the pre-frame fixture shape, kept as a negative control. If this
    ever passes, the guard above has stopped guarding anything — either
    enforcement was disabled or the frame contract was weakened, and the
    green suite would be lying.
    """
    members = [build_neuron(m) for m in STALE_PLAINTEXT_MEMBERS]
    violations = validate_packet(STALE_PLAINTEXT_PACKET, members)
    assert violations, (
        "plain-text proposed_content was ACCEPTED — evidence-frame "
        "enforcement is off or validate_packet no longer checks frames"
    )
    assert any("frame" in v.casefold() for v in violations), violations


@pytest.mark.parametrize(
    "content", [GOLDEN_NVM_CONTENT, TWO_MEMBER_NVM_CONTENT],
    ids=["golden_nvm", "two_member_nvm"],
)
def test_shared_golden_frames_remain_valid_fusion_frames(content):
    """The replay's golden packet body is shared with three test modules.
    Guard it here too, so a frame-contract change cannot quietly invalidate
    the one packet the end-to-end proof is built on."""
    from app.services.evidence_frame import validate_fusion_frame

    assert validate_fusion_frame(content) == []
