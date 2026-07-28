# Carlos Lab integration

This document records how the Nexus Graph and Oracle Funnel from
`carlosbeattyjr/corvus` were adapted onto the current Corvus architecture.
The donor repository is a root import without shared Git ancestry, so these
features are ports, not merged commits.

## Visual contract

Imported experiments live under a visibly separate **Carlos Lab** surface:

- lab accent: magenta `#ff4fd8`
- instrumentation accent: cyan `#45e6ff`
- every imported page carries a `CARLOS LAB` banner
- the existing Universe and Eval Runs pages remain unchanged

The color boundary is functional: it tells the operator when they are
looking at an experimental alternate surface rather than a current Corvus
production view.

## Collision ledger

| Donor assumption | Current Corvus | Resolution |
|---|---|---|
| `ClientShell` owns the application and the bare URL | The current product uses the Perch/window manager and mobile shell | Add two normal windows and a Carlos Lab navigation group; do not replace either shell |
| Nexus replaces the older graph | Current Corvus has a richer Three.js `NeuronUniverse` | Keep Universe intact; Nexus is an alternate 2D projected-shell lens |
| Nexus loads `/neurons/tree` and a profile-specific `apiUrl` | Current `/neurons/graph-3d` is active-only, flat, richer, and already authenticated through `api.ts` | Build Nexus directly from `Graph3DNode[]`; this also avoids rendering inactive tree rows |
| Semantic communities are “Louvain” and Haiku-named on request | Current clustering is deterministic Leiden; the dev-tool north star prohibits server-owned LLM calls | Use `/neurons/clusters` and its deterministic `suggested_label`; no naming LLM or new endpoint |
| Current Leiden service imports `igraph` and `leidenalg`, but neither package was declared | The endpoint returned HTTP 500 in the live service even though isolated feature tests passed | Restore both runtime declarations in `backend/requirements.txt` and cover the import/build path with a regression test |
| Contributor drill-down depends on a new `neurons.created_by` column | Current `Neuron` has no creator attribution and these two features do not require it | Defer Admin Universe/people drill-down; no opportunistic schema migration |
| Donor LoCoMo harness predates the answer/verifier split and current retrieval telemetry | Current harness has a newer verifier policy and telemetry contract | Preserve both. Add `recall_hits_with_context`; keep the old `recall_hits` return shape for analysis scripts |
| Funnel is always on | Current certificate comparability favors explicit experimental arms | Add opt-in `--oracle-funnel`; it observes the exact context and cannot change the answer |
| Funnel output is only JSONL in an eval directory | Current UI has immutable eval surfaces but no LoCoMo artifact reader | Add a read-only artifact service and Carlos Lab viewer for the latest real funnel file |
| Donor root contains broad product/CI changes | Current main includes later roadmap/admission and memory-focus work | Import no root shell, CI, roadmap, write-gate, janitor, or reconsolidation changes |

## Acceptance contract

### Nexus Graph

- loads active neurons and coverage-first edges from `/neurons/graph-3d`
- renders deterministic domain, layer, and neuron shells
- supports orbit, drag, zoom, hover neighborhood tracing, and neuron detail
- toggles cross-scope, local, and Leiden-community overlays
- remains visually and navigationally distinct from Universe

### Oracle Funnel

- classifies each non-adversarial LoCoMo question as `ingest`,
  `candidate`, `rank`, `assembly`, `synthesis`, `judge`, or `success`
- uses the exact `PreparedContext` that produced the answer
- retains current retrieval telemetry and verifier-split behavior
- makes zero additional LLM calls
- writes per-question JSONL and a ledger into the existing result payload
- exposes only existing artifacts through a read-only admin endpoint

## Deliberately deferred donor features

This slice does not import tiered admission, reranking, prospective memory,
enterprise fan-out, benchmark registries, processing-mode overrides, or the
donor landing shell. Those change production behavior or product direction
and should be evaluated independently after the two diagnostic features are
understood.

## Live proof notes

- The production system-use notification is asynchronous. Browser proof must
  wait for `/admin/system-banner`, acknowledge the visible notice, and only
  then interact with the Perch.
- The current frontend attempts to load Google Fonts while its Content
  Security Policy permits only self-hosted styles. The browser blocks that
  request and falls back to local fonts. This predates Carlos Lab and does not
  impair either imported feature; it remains a separate security/style cleanup.
