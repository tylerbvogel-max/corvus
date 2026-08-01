"""Golden NVM replay on a throwaway Postgres database (kernel Phase 6.1 prep).

Loads the frozen incident fixture into a fresh database, builds the
FusionPlan through the REAL loaders (peer co-fire loader over
neuron_firings), routes it through the REAL one-step lifecycle
(proposed -> approve_and_apply, one transaction, real action bus + SQL),
and asserts every postcondition against actual DB state. Then proves the
two Phase 4 guarantees live: idempotent replay via the action-bus plan
hash, and terminal supersession of a second proposal aimed at dead state.

Run (preferred — this is the `kernel-replay` lane):
  cd ~/Projects/corvus/backend && \
  REPLAY_DB=corvus_test_kernel_replay TENANT_ID=corvus-mind \
  venv/bin/python scripts/run_test_lane.py kernel-replay

Or directly:
  cd ~/Projects/corvus/backend && \
  REPLAY_DB=corvus_test_kernel_replay TENANT_ID=corvus-mind PYTHONPATH=. \
  venv/bin/python tests/replay_nvm_throwaway.py

The database named by REPLAY_DB is DROPPED and recreated. Never point it
at corvus_mind. It must already exist (the yggdrasil role lacks CREATEDB):
  sudo -u postgres psql -c \
    'CREATE DATABASE corvus_test_kernel_replay OWNER yggdrasil'

THE PACKETS THIS SCRIPT FEEDS THE KERNEL LIVE ELSEWHERE, ON PURPOSE:
tests/kernel_matrix_fixtures.py. They are validated on every PR by
tests/test_kernel_replay_fixture_guard.py, because this script is expensive,
opt-in, and spent weeks silently broken when those packets went stale
(kernel-replay-ungated). If this script fails on a packet violation, the
fixture is stale and that guard should have said so first.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

REPLAY_DB = os.environ.get("REPLAY_DB", "corvus_test_kernel_replay")
assert REPLAY_DB != "corvus_mind", "refusing to run against the live database"
# Connection parameters are overridable so this can run somewhere other than
# one developer's laptop. The defaults are the local yggdrasil role, so the
# documented command line is unchanged; CI supplies a postgres superuser
# against a service container instead.
REPLAY_DB_USER = os.environ.get("REPLAY_DB_USER", "yggdrasil")
REPLAY_DB_PASSWORD = os.environ.get("REPLAY_DB_PASSWORD", "yggdrasil")
REPLAY_DB_HOST = os.environ.get("REPLAY_DB_HOST", "localhost")
REPLAY_DB_PORT = os.environ.get("REPLAY_DB_PORT", "5432")
os.environ["DATABASE_URL"] = (
    f"postgresql+asyncpg://{REPLAY_DB_USER}:{REPLAY_DB_PASSWORD}"
    f"@{REPLAY_DB_HOST}:{REPLAY_DB_PORT}/{REPLAY_DB}"
)
os.environ.setdefault("TENANT_ID", "corvus-mind")

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nvm_incident"
CORE = [47, 51, 57, 177, 1151, 1161]
MATRIX_IDS = list(range(5001, 5010))


def _load(name: str):
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _dt(v):
    return datetime.fromisoformat(v) if v else None


async def _recreate_database() -> None:
    """Reset the throwaway DB's schema. The database itself must already
    exist. Locally the yggdrasil role lacks CREATEDB, so create it once with
    `sudo -u postgres psql -c 'CREATE DATABASE <name> OWNER yggdrasil'`; in CI
    the postgres service container creates it from POSTGRES_DB."""
    import asyncpg
    conn = await asyncpg.connect(
        user=REPLAY_DB_USER, password=REPLAY_DB_PASSWORD, database=REPLAY_DB,
        host=REPLAY_DB_HOST, port=int(REPLAY_DB_PORT))
    await conn.execute("DROP SCHEMA public CASCADE")
    await conn.execute("CREATE SCHEMA public")
    await conn.close()


async def _seed(db) -> None:
    from app.models import (
        AutopilotProposal, MindPairVerdict, Neuron, NeuronEdge, NeuronFiring,
        ProposalItem, Query, SynapticLearningEvent,
    )

    for q in _load("queries"):
        db.add(Query(id=q["id"], user_message=q.get("user_message") or "…",
                     created_at=_dt(q.get("created_at"))))
    member_rows = _load("neurons")
    member_ids = {n["id"] for n in member_rows}
    for n in member_rows:
        db.add(Neuron(
            id=n["id"], layer=n["layer"], node_type=n["node_type"],
            label=n["label"], content=n["content"], summary=n["summary"],
            department=n["department"], invocations=n["invocations"],
            avg_utility=n["avg_utility"], is_active=n["is_active"],
            superseded_by=None,  # set after all rows exist (self-FK)
            authority_level=n["authority_level"], embedding=n["embedding"],
            entities=n.get("entities"), centrality=n.get("centrality", 0.0),
            created_at=_dt(n.get("created_at")),
        ))
    # Stub peers for every edge endpoint outside the fixture members.
    edges = _load("neuron_edges")
    peer_ids = ({e["source_id"] for e in edges}
                | {e["target_id"] for e in edges}) - member_ids
    for pid in sorted(peer_ids):
        db.add(Neuron(id=pid, layer=3, node_type="lesson",
                      label=f"peer-{pid}", department="Environment",
                      is_active=True))
    await db.flush()
    for n in member_rows:
        if n["superseded_by"] is not None:
            row = await db.get(Neuron, n["id"])
            row.superseded_by = n["superseded_by"]
    for e in edges:
        db.add(NeuronEdge(
            source_id=e["source_id"], target_id=e["target_id"],
            co_fire_count=e["co_fire_count"], weight=e["weight"],
            edge_type=e["edge_type"], source=e.get("source"),
            context=e.get("context")))
    member_queries = []
    for f in _load("neuron_firings"):
        db.add(NeuronFiring(
            neuron_id=f["neuron_id"], query_id=f["query_id"],
            created_at=_dt(f.get("created_at"))))
        if f["neuron_id"] in {47, 51, 57, 177, 1151, 1161} \
                and f["query_id"] not in member_queries:
            member_queries.append(f["query_id"])
    # Give peer #9 real co-fire history on 12 member queries so the
    # external-edge recomputation path runs against actual evidence:
    # union=12 -> weight 12/20=0.6, promoted.
    for q in member_queries[:12]:
        db.add(NeuronFiring(neuron_id=9, query_id=q))
    for ev in _load("synaptic_learning_events"):
        db.add(SynapticLearningEvent(
            id=ev["id"], query_id=ev["query_id"], neuron_id=ev["neuron_id"],
            event_type=ev["event_type"],
            old_avg_utility=ev["old_avg_utility"],
            new_avg_utility=ev["new_avg_utility"], delta=ev["delta"],
            effective_delta=ev["effective_delta"],
            combined_score=ev["combined_score"],
            attribution_weight=ev["attribution_weight"],
            outcome=ev["outcome"], created_at=_dt(ev.get("created_at"))))
    proposal_ids = set()
    for p in _load("autopilot_proposals"):
        proposal_ids.add(p["id"])
        db.add(AutopilotProposal(
            id=p["id"], state=p["state"], gap_source=p.get("gap_source"),
            gap_description=p.get("gap_description"),
            applied_at=_dt(p.get("applied_at")),
            reviewed_by=p.get("reviewed_by")))
    await db.flush()
    for item in _load("proposal_items"):
        if item["proposal_id"] not in proposal_ids:
            continue  # fixture carries items of unrelated proposals too
        db.add(ProposalItem(
            id=item["id"], proposal_id=item["proposal_id"],
            action=item["action"],
            target_neuron_id=item.get("target_neuron_id"),
            field=item.get("field"), old_value=item.get("old_value"),
            new_value=item.get("new_value"), reason=item.get("reason")))
    for v in _load("mind_pair_verdicts"):
        db.add(MindPairVerdict(
            id=v["id"], neuron_a_id=v["neuron_a_id"],
            neuron_b_id=v["neuron_b_id"], sim=v["sim"], verdict=v["verdict"],
            source=v.get("source"), content_hash_a=v.get("content_hash_a"),
            content_hash_b=v.get("content_hash_b"), detail=v.get("detail")))
    await db.commit()


async def _queue_with_packet(db, members, packet):
    """Exercise the real janitor queue boundary with only the LLM replaced."""
    from app.services.reconsolidation import review as rv
    from app.services.mind_janitors import _queue_component_proposal

    async def _mock_review(members_, pair_verdicts=None):
        return packet

    real_review = rv.review_component
    rv.review_component = _mock_review
    try:
        result = await _queue_component_proposal(
            db, members[0], members[1:], None, {})
    finally:
        rv.review_component = real_review
    # The queue returns an outcome-shaped dict, so a rejected packet used to
    # surface as `KeyError: 'disposition'` several lines later — a stale
    # FIXTURE misreported as a broken kernel. Name it where it happens.
    if result.get("outcome") == "review_failed":
        raise AssertionError(
            "packet was REFUSED by validate_packet before the kernel was "
            "exercised — the fixture is stale, not the kernel: "
            + "; ".join(result.get("violations") or ["<no violations given>"]))
    return result


async def _phase5_matrix(db, identity) -> dict:
    """Four missing Phase-5 integration rows against real SQL/actions."""
    from app.models import Action, AutopilotProposal, Neuron, NeuronEdge
    from app.services import action_bus
    from app.services.proposal_apply_service import ProposalApplyError
    from app.services.reconsolidation.lifecycle import approve_and_apply
    from sqlalchemy import func, select
    from tests.kernel_matrix_fixtures import build_neuron, case

    strict_case = case("strict_pair_retain_canonical")
    strict = [build_neuron(m) for m in strict_case.members]
    db.add_all(strict)
    await db.flush()
    strict_result = await _queue_with_packet(db, strict, strict_case.packet)
    assert strict_result["disposition"] == "retain-canonical"
    assert strict_result["proposal_id"]
    await db.commit()
    strict_proposal = await db.get(AutopilotProposal, strict_result["proposal_id"])
    await approve_and_apply(db, strict_proposal, identity,
                            notes="phase5 strict-pair countersign")
    await db.commit()
    strict_a = await db.get(Neuron, 5001)
    strict_b = await db.get(Neuron, 5002)
    assert strict_a.is_active and strict_b.superseded_by == 5001 \
        and not strict_b.is_active

    scoped_case = case("scoped_truths_no_fuse")
    scoped = [build_neuron(m) for m in scoped_case.members]
    db.add_all(scoped)
    await db.flush()
    proposals_before = (await db.execute(
        select(func.count()).select_from(AutopilotProposal))).scalar_one()
    scoped_result = await _queue_with_packet(db, scoped, scoped_case.packet)
    proposals_after = (await db.execute(
        select(func.count()).select_from(AutopilotProposal))).scalar_one()
    assert scoped_result["outcome"] == "abstain"
    assert proposals_after == proposals_before, "scoped truths must not queue fusion"

    conflict_case = case("contradictory_members_abstain")
    contradiction = [build_neuron(m) for m in conflict_case.members]
    db.add_all(contradiction)
    await db.flush()
    proposals_before_conflict = proposals_after
    conflict_result = await _queue_with_packet(db, contradiction,
                                               conflict_case.packet)
    proposals_after_conflict = (await db.execute(
        select(func.count()).select_from(AutopilotProposal))).scalar_one()
    assert conflict_result["outcome"] == "abstain"
    assert proposals_after_conflict == proposals_before_conflict

    rollback_case = case("mid_transaction_rollback")
    rollback_members = [build_neuron(m) for m in rollback_case.members]
    db.add_all(rollback_members)
    await db.flush()
    rollback_result = await _queue_with_packet(db, rollback_members,
                                               rollback_case.packet)
    assert rollback_result["disposition"] == "synthesize-new"
    await db.commit()

    before = {
        "neurons": (await db.execute(select(func.count()).select_from(Neuron))).scalar_one(),
        "edges": (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar_one(),
        "actions": (await db.execute(select(func.count()).select_from(Action))).scalar_one(),
    }
    rollback_proposal = await db.get(AutopilotProposal, rollback_result["proposal_id"])
    original_rewire = action_bus._action_registry.get("edge.rewire")

    async def _planted_failure(payload, actor, db_, action_row):
        raise RuntimeError("PHASE5_PLANTED_EDGE_REWIRE_FAILURE")

    action_bus._action_registry._handlers["edge.rewire"] = replace(
        original_rewire, handler=_planted_failure)
    failure = None
    try:
        await approve_and_apply(db, rollback_proposal, identity,
                                notes="this approval must roll back")
    except ProposalApplyError as exc:
        failure = str(exc)
        await db.rollback()
    finally:
        action_bus._action_registry._handlers["edge.rewire"] = original_rewire
    assert failure and "PHASE5_PLANTED_EDGE_REWIRE_FAILURE" in failure

    rollback_proposal = await db.get(AutopilotProposal, rollback_result["proposal_id"])
    after = {
        "neurons": (await db.execute(select(func.count()).select_from(Neuron))).scalar_one(),
        "edges": (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar_one(),
        "actions": (await db.execute(select(func.count()).select_from(Action))).scalar_one(),
    }
    rollback_after = [await db.get(Neuron, nid) for nid in (5007, 5008, 5009)]
    assert after == before, f"full rollback count drift: before={before}, after={after}"
    assert rollback_proposal.state == "proposed" and rollback_proposal.reviewed_by is None
    assert all(n.is_active and n.superseded_by is None for n in rollback_after)

    return {
        "strict_pair_retain_canonical": {
            "proposal_id": strict_result["proposal_id"], "canonical_id": 5001,
            "absorbed_id": 5002,
        },
        "scoped_truths_no_fuse": scoped_result,
        "contradictory_members_abstain": conflict_result,
        "mid_transaction_rollback": {
            "proposal_id": rollback_result["proposal_id"],
            "planted_failure": failure, "counts_before": before,
            "counts_after": after, "proposal_state": rollback_proposal.state,
        },
    }


def _golden_packet() -> dict:
    sys.path.insert(0, str(Path(__file__).parent))
    from test_reconsolidation_apply import _golden_packet as gp
    return gp()


async def main() -> None:
    print(f"== recreating throwaway database {REPLAY_DB}")
    await _recreate_database()

    from app.database import async_session, engine
    from app.models import Base, Neuron, NeuronEdge, AutopilotProposal, ProposalItem
    from app.services.actions.init_registry import init_actions_registry
    from sqlalchemy import func, select, text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    init_actions_registry()

    receipts: dict = {"db": REPLAY_DB}
    async with async_session() as db:
        print("== seeding frozen fixture")
        await _seed(db)

        from app.services.reconsolidation import review as rv
        from app.services.reconsolidation.lifecycle import (
            ProposalStaleError, approve_and_apply, reconsolidation_item_spec,
        )
        from app.services.reconsolidation.loaders import (
            build_plan_for_component,
        )

        identity = SimpleNamespace(user_id="tyler-replay", role="admin",
                                   source="replay")
        print("== Phase 5 integration matrix")
        receipts["phase5_matrix"] = await _phase5_matrix(db, identity)

        members = [(await db.get(Neuron, mid)) for mid in CORE]
        print("== building FusionPlan through real loaders")
        packet = _golden_packet()

        async def _mock_review(members_, pair_verdicts=None):
            return packet

        real_review = rv.review_component
        rv.review_component = _mock_review  # LLM boundary only
        try:
            plan = await build_plan_for_component(db, members)
        finally:
            rv.review_component = real_review
        receipts["plan"] = {
            "disposition": plan.disposition.value,
            "plan_hash": plan.plan_hash(),
            "union_invocations": plan.inheritance.invocations_union_distinct,
            "utility_replayed": plan.inheritance.utility_replayed,
            "internal_edges_to_retire":
                len(plan.rewiring.internal_activation_edges_to_retire),
            "external_peers": len(plan.rewiring.external_peers),
            "provenance_gaps": plan.inheritance.utility_provenance_gaps,
        }
        assert plan.disposition.value == "synthesize-new"
        assert plan.inheritance.invocations_union_distinct == 419, \
            f"union must be 419, got {plan.inheritance.invocations_union_distinct}"
        assert len(plan.rewiring.internal_activation_edges_to_retire) == 12

        print("== queueing component_fusion proposal (one reconsolidate item)")
        proposal = AutopilotProposal(
            state="proposed", gap_source="component_fusion",
            gap_description="replay: NVM golden component")
        db.add(proposal)
        await db.flush()
        db.add(ProposalItem(
            proposal_id=proposal.id, action="reconsolidate",
            neuron_spec_json=reconsolidation_item_spec(plan),
            reason="golden replay"))
        await db.commit()
        proposal_id = proposal.id

    async with async_session() as db:
        print("== ONE-step review: approve = approve+apply, one transaction")
        p = await db.get(AutopilotProposal, proposal_id)
        identity = SimpleNamespace(user_id="tyler-replay", role="admin",
                                   source="replay")
        has_edges = await approve_and_apply(db, p, identity,
                                            notes="golden replay countersign")
        await db.commit()
        assert p.state == "applied" and has_edges

    async with async_session() as db:
        print("== verifying postconditions against real DB state")
        synth = (await db.execute(
            select(Neuron).where(Neuron.source_origin == "reconsolidation")
        )).scalar_one()
        comp = [*CORE, synth.id]
        active = [n for n in
                  (await db.execute(select(Neuron).where(Neuron.id.in_(comp),
                                                         Neuron.is_active))
                   ).scalars().all()]
        internal_conducting = (await db.execute(
            select(func.count()).select_from(NeuronEdge).where(
                NeuronEdge.source_id.in_(comp), NeuronEdge.target_id.in_(comp),
                NeuronEdge.edge_type.in_(("pyramidal", "stellate"))))
        ).scalar_one()
        links = (await db.execute(
            select(NeuronEdge).where(NeuronEdge.target_id == synth.id,
                                     NeuronEdge.edge_type == "evidence-link"))
        ).scalars().all()
        member_embeddings = {n["embedding"] for n in _load("neurons")
                             if n["id"] in set(CORE)}
        superseded_states = {
            pid: (await db.get(AutopilotProposal, pid)).state
            for pid in (1067, 1069, 1071)}
        members_after = [(await db.get(Neuron, mid)) for mid in CORE]

        assert [n.id for n in active] == [synth.id], \
            f"exactly one active representation expected, got {[n.id for n in active]}"
        assert internal_conducting == 0, \
            f"{internal_conducting} internal conducting edges remain"
        assert synth.invocations == 419
        assert {e.source_id for e in links} >= set(CORE)
        assert synth.embedding and synth.embedding not in member_embeddings
        assert all(m.superseded_by == synth.id and not m.is_active
                   for m in members_after)
        assert all(s == "superseded" for s in superseded_states.values()), \
            f"approved-unapplied must retire terminally: {superseded_states}"
        # External edge to peer #9 recomputed from union evidence, never
        # from the old fixture weight.
        peer_edge = (await db.execute(
            select(NeuronEdge).where(
                NeuronEdge.source_id.in_((9, synth.id)),
                NeuronEdge.target_id.in_((9, synth.id)))
        )).scalar_one()
        assert peer_edge.co_fire_count == 12, \
            f"union co-fire must be 12, got {peer_edge.co_fire_count}"
        assert abs(peer_edge.weight - 0.6) < 1e-9
        assert peer_edge.edge_type in ("stellate", "pyramidal")

        centrality = (await db.execute(text(
            "SELECT id, centrality, is_active FROM neurons "
            "WHERE id = ANY(:ids) ORDER BY centrality DESC"),
            {"ids": comp})).all()
        by_id = {r.id: r.centrality for r in centrality}
        # Provenance is not topology: the living synthesis must outrank
        # every absorbed corpse after the conducting-only recompute.
        assert all(by_id[synth.id] > by_id[m] for m in CORE), \
            f"corpses still outrank the synthesis: {by_id}"
        receipts["post"] = {
            "synthesis_id": synth.id,
            "synthesis_label": synth.label,
            "invocations": synth.invocations,
            "avg_utility": synth.avg_utility,
            "authority_level": synth.authority_level,
            "effective_date": str(synth.effective_date),
            "last_verified": str(synth.last_verified),
            "entities": synth.entities,
            "active_representations": [n.id for n in active],
            "internal_conducting_edges": internal_conducting,
            "provenance_links": sorted(e.source_id for e in links),
            "superseded_proposals": superseded_states,
            "centrality_top": [[r.id, r.centrality, r.is_active]
                               for r in centrality[:3]],
        }

        print("== idempotent replay: same plan hash through the action bus")
        from app.services import action_bus
        replay = await action_bus.submit(
            db=db, kind="proposal.reconsolidate",
            actor=SimpleNamespace(user_id="tyler-replay"),
            actor_type="user",
            idempotency_key=f"fusionplan:{plan.plan_hash()}",
            input_data={"proposal_id": proposal_id, "item_id": None,
                        "fusion_plan": plan.model_dump(mode="json"),
                        "member_state_hash": plan.member_state_hash(),
                        "actor_type": "user"})
        synth_count = (await db.execute(
            select(func.count()).select_from(Neuron).where(
                Neuron.source_origin == "reconsolidation"))).scalar_one()
        assert replay.state == "applied" and \
            replay.audit.get("survivor_id") == synth.id
        assert synth_count == 1, "replay must not create a second synthesis"
        receipts["idempotent_replay"] = {
            "state": replay.state, "survivor_id": replay.audit["survivor_id"],
            "synthesis_count_after_replay": synth_count}

        print("== stale second proposal terminally supersedes at review")
        p2 = AutopilotProposal(state="proposed", gap_source="component_fusion",
                               gap_description="replay: stale duplicate plan")
        db.add(p2)
        await db.flush()
        db.add(ProposalItem(proposal_id=p2.id, action="reconsolidate",
                            neuron_spec_json=reconsolidation_item_spec(plan),
                            reason="stale replay"))
        await db.flush()
        await db.refresh(p2)
        try:
            await approve_and_apply(db, p2, SimpleNamespace(user_id="tyler-replay"))
            raise AssertionError("stale plan must not apply")
        except ProposalStaleError as exc:
            await db.commit()
            receipts["stale_second_proposal"] = {
                "proposal_id": p2.id, "state": p2.state,
                "violations": exc.violations[:3]}
        assert p2.state == "superseded"

    print("\n== ALL REPLAY ASSERTIONS PASSED ==")
    print(json.dumps(receipts, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
