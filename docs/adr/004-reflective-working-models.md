# ADR 004: Reflective working models

- Status: Implemented
- Date: 2026-09-27
- Roadmap: `corvus-long-horizon / mind-reflective-models`

## Decision

The daily compiler job is the reflection cycle. It derives a reusable procedure
from a cluster of active, evidence-gated lessons, then records a conditional
claim, a prospective prediction, a falsifier, and a safe read-only probe. The
independent critic must admit the synthesis before it can be projected as a
native `mind-*` skill. The graph's source lessons remain the facts; the model
and skill are revisable artifacts.

The same cycle compares admitted models with later lessons from the same scope.
A later lesson is eligible only when its cited session is available and the
episode does not show the model skill being loaded or pointed to, or a source
lesson being injected. The reviewer must cite an exact quote and a separate
critic must approve its interpretation. A falsifying observation marks the
model `challenged` and archives the projected skill after the graph transaction
commits. Ambiguous evidence leaves the model unchanged. Skill loads and pointer
counts are exposure signals, never proof of an outcome.

The versioned, atomically written `reflection-models.json` owns admission and
review state. It migrates the old declined-candidate receipts on first load.
`GET /compile/models` exposes claims, falsifiers, source quotes, observations,
and state history without changing the catalog. The old decline-state writer
is retired. The auditor continues to judge individual neuron fidelity; the
compiler timer remains the only schedule for cross-lesson reflection and skill
projection. No new timer, model-as-neuron, or model-generated command executor
is introduced.

## Trust limits

An absent or malformed catalog aborts publication if migration data is corrupt.
Missing episode evidence is treated as uncertain, not independent. The episode
filter can only reason about exposures the harness logs; it cannot prove an
unrecorded skill was never read. The catalog therefore records *evidence of a
challenge*, not an independently measured performance lift. A challenged
model cannot automatically republish from the same source fingerprint; changed
source content creates a new candidate for review.

The graph commit precedes file projection. Retractions archive old skill files,
then catalog admission is saved before any new native skill is published. A
projection failure can leave an admitted catalog record and graph shadow ahead
of its file; the next compiler run retries publication from the source cluster.
