"""Step 04 — hand classification of the historical staleness supersessions.

Rubric (applied to each unique (conv, newer, older) pair; the OLDER neuron is
the casualty — it received superseded_by + a utility demotion):

  older_kind:
    durable    — standing attribute, relationship, possession, achievement,
                 or completed-event record; does not expire with time
    perishable — plan, intention, in-flight status, 'current' state
    unknown    — casualty label unrecoverable from append-only artifacts

  pair_relation:
    compatible          — no contradiction; both can stand (misfire)
    near_duplicate      — same fact restated; dedup lane's job, vocabulary
                          survives in the newer neuron
    state_update        — newer plausibly updates older perishable state;
                          recency supersession is the designed behaviour
    standing_conflict   — genuine contradiction between two durable claims;
                          recency cannot arbitrate which is true
    unknown             — labels insufficient

  defensible: True only for state_update (and near_duplicate counted
  separately — wrong lane, but the fact survives).

Sources: unique_pairs.json (labels harvested from certificate summary JSONs +
artifact-dir janitor logs). Judgments are the session's own, recorded with
rationale so they can be audited line by line.
"""
import json
import os

ART = os.path.expanduser(
    "~/.corvus-mind/evals/locomo/20260726T155942Z-step04-supersession-forensics")

# (conv, newer, older): (older_kind, pair_relation, rationale)
J = {
    (0, 9, 8): ("unknown", "unknown", "no labels recoverable"),
    (0, 127, 114): ("durable", "compatible",
                    "'Melanie has kids' vs family camping trip — unrelated; "
                    "casualty is a durable family-event record"),
    (1, 34, 12): ("perishable", "state_update",
                  "aspiration ('envisions studio') plausibly updated by "
                  "concrete progress (shared studio-space photo)"),
    (1, 73, 49): ("durable", "near_duplicate",
                  "'owns a fashion store' vs 'runs online clothing store' — "
                  "same standing fact; dedup lane's job"),
    (1, 153, 57): ("durable", "compatible",
                   "'Jon runs a dance studio' (standing) demoted by the "
                   "opening-date event record — complementary facts"),
    (2, 53, 51): ("durable", "compatible",
                  "microphone photo vs notebook photo — two distinct "
                  "episodic records, no conflict"),
    (2, 331, 330): ("unknown", "unknown", "no labels recoverable"),
    (3, 28, 22): ("unknown", "unknown", "no labels recoverable"),
    (3, 50, 47): ("durable", "compatible",
                  "'new screenplay topic' vs 'screenplay is her first' — "
                  "compatible statements about the same project"),
    (3, 50, 46): ("durable", "standing_conflict",
                  "'is her first' vs 'writing second screenplay' — genuine "
                  "contradiction; recency cannot pick which extraction is "
                  "right"),
    (3, 151, 149): ("durable", "compatible",
                    "casualty 'Nate won regional gaming tournament' is a "
                    "durable achievement; 'met friends at tournament' does "
                    "not contradict it"),
    (3, 157, 154): ("perishable", "state_update",
                    "two formulations of the same upcoming-hike plan"),
    (4, 10, 7): ("durable", "compatible",
                 "locker-room photo vs jerseys photo — distinct episodic "
                 "records"),
    (4, 168, 165): ("durable", "near_duplicate",
                    "bookshelf photo restated; fact survives in newer"),
    (4, 166, 165): ("durable", "compatible",
                    "desk photo vs bookcase photo — distinct records"),
    (4, 168, 21): ("durable", "near_duplicate",
                   "same bookshelf photo fact restated"),
    (5, 154, 114): ("unknown", "unknown", "no labels recoverable"),
    (5, 198, 150): ("durable", "near_duplicate",
                    "dogs-outdoors photo restated"),
    (5, 142, 139): ("unknown", "unknown", "no labels recoverable"),
    (5, 150, 44): ("durable", "near_duplicate",
                   "photo-of-four-dogs fact restated"),
    (5, 181, 154): ("unknown", "unknown",
                    "casualty label unrecoverable (newer: two-hour trail "
                    "hike)"),
    (5, 152, 96): ("unknown", "unknown",
                   "casualty label unrecoverable (newer: Andrew wants to "
                   "meet the dogs)"),
    (6, 71, 56): ("durable", "unknown",
                  "casualty 'James exploring RPGs and strategy games' is a "
                  "stable interest; newer label unrecoverable"),
    (6, 61, 60): ("unknown", "unknown", "no labels recoverable"),
    (6, 190, 19): ("unknown", "unknown", "no labels recoverable"),
    (6, 178, 144): ("unknown", "unknown", "no labels recoverable"),
    (6, 295, 294): ("durable", "compatible",
                    "casualty 'John joined online programming group' is a "
                    "standing membership; collaboration does not contradict "
                    "it"),
    (7, 69, 1): ("durable", "compatible",
                 "casualty 'finished electrical engineering project' is a "
                 "completed-work record; a later breakthrough coexists"),
    (7, 271, 40): ("perishable", "state_update",
                   "'is not currently doing yoga' is current-state; later "
                   "evidence of practice legitimately updates it"),
    (7, 340, 298): ("perishable", "unknown",
                    "casualty 'planning meditation retreat' is a plan; "
                    "newer label unrecoverable"),
    (7, 353, 276): ("durable", "compatible",
                    "casualty 'Deborah attended yoga retreat' is a "
                    "completed-event record; 'practices yoga' coexists"),
    (8, 38, 30): ("perishable", "state_update",
                  "'agreed to try suggestions' is an intent; 'tried seltzer "
                  "before' plausibly moves it forward"),
    (8, 42, 30): ("perishable", "state_update",
                  "same casualty as above, second newer neuron; repeat of "
                  "the same adjudication"),
    (8, 135, 113): ("unknown", "unknown",
                    "casualty label unrecoverable (newer: Evan injured "
                    "knee)"),
    (8, 112, 93): ("durable", "compatible",
                   "casualty 'feels more energized on diet' is an "
                   "experience report; starting the routine does not "
                   "contradict it"),
    (9, 69, 40): ("durable", "near_duplicate",
                  "'opened car maintenance shop' restated with a date; "
                  "fact survives in newer"),
    (9, 82, 28): ("perishable", "state_update",
                  "Boston-trip plan superseded by evolved tour plan"),
    (9, 81, 65): ("perishable", "state_update",
                  "'plan to stay in touch' vs 'hadn't chatted in a while' — "
                  "state progression"),
    (9, 28, 9): ("perishable", "state_update",
                 "'heading to Boston after Japan' plan updated by the "
                 "later Boston-trip plan"),
    (9, 207, 48): ("perishable", "state_update",
                   "visit invitation progressed to a concrete visit plan"),
    (9, 354, 331): ("durable", "compatible",
                    "casualty 'Calvin performed in Boston' is a completed "
                    "performance record; a later accepted invitation does "
                    "not erase it (created_at order even inverts event "
                    "order here)"),
    (9, 107, 22): ("durable", "compatible",
                   "casualty 'Calvin got a new vehicle' is a possession "
                   "fact; 'excited to drive again' does not contradict it"),
}


def main():
    pairs = json.load(open(os.path.join(ART, "step04_unique_pairs.json")))
    rows = []
    for p in pairs:
        key = (p["conv"], p["newer"], p["older"])
        kind, rel, why = J[key]
        rows.append({**p, "older_kind": kind, "pair_relation": rel,
                     "rationale": why,
                     "defensible": rel == "state_update"})
    assert len(rows) == 42, f"expected 42 unique pairs, got {len(rows)}"
    tally = {}
    for r in rows:
        tally[r["pair_relation"]] = tally.get(r["pair_relation"], 0) + 1
    kinds = {}
    for r in rows:
        kinds[r["older_kind"]] = kinds.get(r["older_kind"], 0) + 1
    summary = {
        "unique_pairs": len(rows),
        "actions_including_repeat_fires": 55,
        "repeat_fire_actions": 55 - 42,
        "pair_relation_tally": tally,
        "older_kind_tally": kinds,
        "defensible_state_updates": tally.get("state_update", 0),
        "misfires_compatible": tally.get("compatible", 0),
        "near_duplicates_wrong_lane": tally.get("near_duplicate", 0),
        "standing_conflicts": tally.get("standing_conflict", 0),
        "unknown": tally.get("unknown", 0),
    }
    out = {"summary": summary, "rows": rows}
    path = os.path.join(ART, "step04_historical_classification.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(summary, indent=1))
    print("written:", path)


main()
