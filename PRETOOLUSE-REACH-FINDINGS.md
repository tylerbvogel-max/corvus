# mind-pretooluse-reach — session 3 findings

Record: `mind-pretooluse-reach` (corvus-long-horizon, admitted @ revision 106)
Worktree: `~/Projects/corvus-wt/pretooluse-reach` (branch `wt/pretooluse-reach`, base `e3f9e37`)
Date: 2026-08-01

## Standing condition on every number below

Four other Claude Code sessions ran concurrently on this machine throughout,
all firing hooks into `corvus_mind`. Measured, not assumed: sampling the write
fingerprint every 3s showed the corpus moving twice inside a 15-second window
(`+3 queries / +17 firings / +17 invocations`, then `+1 / +6 / +6`). Every
claim below states which instrument produced it and whether that instrument is
concurrency-proof.

## 1. BEFORE snapshot — `GET /metrics/mind/injection-channels`

Episode-log based, no recall replay, so this is the one number here that the
concurrency cannot distort. Taken 2026-08-01 14:5x UTC.

| trigger | injected | reward | penalty | load-bearing |
|---|---|---|---|---|
| PreToolUse | 1,923 | 325 | 17 | **16.90%** |
| UserPromptSubmit | 5,381 | 225 | 1 | 4.18% |
| capsule:mind-charter | 12,389 | 435 | 0 | 3.51% |
| capsule:mind-self-model | 8,161 | 165 | 0 | 2.02% |
| SessionStart (retired) | 1,307 | 8 | 0 | 0.61% |

Corpus: 1,191 sessions scanned / 836 distilled; pooled 3.95%; standing share
75.28%. Against the record's 449-session baseline (PreToolUse 17.17% on 1,642
injections) the channel has held: **16.90% on 1,923**, and its lead over the
next-best channel has *widened* from 2.5x to 4.0x. All 17 penalties in the
corpus are still PreToolUse's.

Injections per session (interrupt budget, the resource being spent):
1,923 / 836 distilled sessions = **2.30 PreToolUse injections per session**.

Capsule receipts for acceptance criterion #5: charter 12,389 injected / 435
reward, self-model 8,161 / 165. Neither was touched by anything in this
session — no file under `capabilities/skills/` was read or written.

## 2. BLOCKER — the designated after-instrument cannot separate Bash

This is the finding that outranks the rest, and it is structural, not a
measurement.

`harness/claude-code/memory_inject_hook.py:349` logs every injection as

```python
_log_injection(session_id, cwd, event, hits, query_id)   # event == "PreToolUse"
```

and `backend/app/services/injection_channel.py:93` buckets on exactly that
string. The tool name is never written down. So the moment the gate widens,
Bash-triggered and Edit/Write-triggered injections both land in one
`PreToolUse` row, and the record's own guardrail —

> Report the Bash sub-rate SEPARATELY before and after. A pooled PreToolUse
> gain that hides a Bash loss is a failure.

— is unsatisfiable by the instrument the record designates. Acceptance
criterion #3 cannot be met as the system is built. Worse quietly than loudly:
the report would still render a plausible PreToolUse percentage.

There is a second-order effect in the same direction. `reconstruct_history`
drops any neuron whose triggers disagree from the finer breakdown
(`ambiguous_trigger_neurons`, currently **390**). Widening the gate creates a
new way for one neuron to arrive under two triggers within a session, so the
`by_trigger` denominator gets *thinner* exactly when it is being asked to carry
a finer split.

### The fix that preserves comparability

Do **not** stamp `PreToolUse:Bash` as the trigger. That is the in-idiom move
(`capsule:{name}` already composites) and it is wrong here: it would fork the
1,923 historical injections away from the new ones and make before/after a
comparison between two different rows, violating acceptance criterion #4
("the same per-trigger instrument").

Instead: keep `trigger` as `"PreToolUse"` and add a sibling `tool` field to the
injection record, then teach `injection_channel` to emit a `by_tool` sub-split
*within* PreToolUse. Records written before the change carry no `tool` field
and are read as Bash — which is not a default, it is a fact: the gate admitted
nothing else. That yields one continuous Bash sub-rate series across the
boundary, which is precisely what criterion #3 asks for.

**This is a prerequisite for directions (a), (b) and (c). None of them can be
accepted without it.** It is not a prerequisite for (d).

### Built and verified here (not shipped)

`memory_inject_hook._log_injection` now takes `tool=` and writes it only for
PreToolUse; `injection_channel` grew a `per_tool` index, `_tools_by_neuron`,
`_tools_for_line` (marker join, used for stamped and reconstructed lines alike
— the distiller stamps channel and trigger but never the tool), and a
`pretooluse_by_tool` block, plus `ambiguous_tool_units` for the refuse-to-guess
cases. The sub-split reuses `by_trigger`'s unit rule exactly: only neurons a
session saw *solely* via PreToolUse are counted, so the parts reconcile with
the whole instead of being a second number.

Receipt — same call, real corpus, 1,191 sessions / 836 distilled:

```
pooled by_trigger.PreToolUse   injected 1923  reward 325  penalty 17  16.90%
pretooluse_by_tool.Bash        injected 1923  reward 325  penalty 17  16.90%
ambiguous_tool_units 0
capsule:mind-charter    12389 / 435 / 0   (unchanged)
capsule:mind-self-model  8161 / 165 / 0   (unchanged)
```

The Bash sub-rate reproduces the historical series *exactly*, which is the
whole point: after the widening, that column keeps meaning what it meant on
2026-08-01 rather than starting from zero.

Tests: `backend/tests/test_injection_channel.py`, **25 passed** (20 pre-existing
+ 5 new): legacy no-`tool` records read as Bash; a widened gate whose Edit
injections earn nothing cannot dilute a healthy Bash rate into a plausible
pooled 16.7%; the sub-split sums to the trigger row it splits; a neuron
delivered by two tools in one session is credited to neither; no trigger other
than PreToolUse can appear in the split.

## 3. The frozen-fixture path is dead against current `main`

The record's guardrail sends all recall measurement through
`~/.corvus-mind/evals/recall-probe/probe.py`, and under concurrency the only
usable target is a frozen fixture. All three existing fixtures
(`f9b85881-replay@v1/v2`, `f9b85881-fidelity@v1`, `index-staleness@v1`) are now
unusable, and neither horn of the dilemma is a measurement of the live system:

- **Serve at today's HEAD** → dies in startup. The 2026-07-31 build added
  fail-closed schema enforcement (`backend/app/services/schema_authority.py`):
  `SchemaAuthorityError: observed unmanaged (no alembic_version); expected
  025_standard_date_seed`. Those dumps were taken from a `corvus_mind` that had
  no `alembic_version` table. (Live `corvus_mind` *is* at `025` today.)
- **Serve at the sha that captured them** (`3f28826`) → runs a scorer nobody
  ships. Between `3f28826` and `e3f9e37`, `prefilter_score_stage.py`,
  `spread_stage.py` and `inhibitory_stage.py` all changed.

So `mind-recall-fixture`'s artifacts have a shelf life measured against the
schema contract, and nothing warned when it expired.

### Fixed here: capture no longer requires a quiet machine

`create_fixture()` required `quiesce()` — a still corpus — which on this box is
not reachable. Added `capture_mode="restore"`: skip the quiet window, and read
the fingerprint *out of the dump* by restoring it into a throwaway database and
fingerprinting that. `pg_dump` already takes a transactionally consistent
snapshot, so the bytes are coherent regardless; what `quiesce` actually buys is
confidence that the recorded number matches those bytes, and restoring proves
it directly instead of inferring it from stillness. Cost: one extra restore.
`capture_mode` is recorded in the manifest. The default path is byte-identical
to before.

Proven by use: **`pretooluse-reach@v1` captured, 35.2 MB, sha256 `09481a623e8e…`,
`T=2026-08-01 15:05:04`**, under full concurrent load, and it *served* cleanly
at `e3f9e37` — the schema check passed, confirming a fresh capture was the right
diagnosis. The dump is on disk and reusable.

## 4. Why the distribution measurement did not complete

`path_score_distribution.py` (written, working, committed here) implements
verification criterion #1. It ran through fixture capture and backend startup
and then died on the 339 recalls:

```
oom-kill: … task=uvicorn,pid=10466 …
Out of memory: Killed process 10466 (uvicorn) total-vm:7487476kB, anon-rss:828376kB
```

6.4 GB box, ~1.0 GB available with four agent sessions plus the live
`corvus-mind` backend resident. A second backend process does not fit. The live
`corvus-mind` service was then OOM-killed twice more on its own during this
session (pids 10664, 11897), each restart taking ~2 minutes to reload models —
which is also why the roadmap gate blocked every mutation for a stretch: it
needs a backend to re-admit against, and the backend was the thing that died.

The script carries a LIVE fallback with the corpus drift measured across the
run and comparability marked accordingly. It is deliberately **not** offered as
a substitute for the record's after-measurement.

One thing the fallback needed and now has a receipt for: the live backend
(started 10:09) is running `~/Projects/corvus` with a **dirty**
`scoring_engine.py`. The diff is a pure vocabulary lift — the tuple
`("burst", "impact", "precision", "novelty", "recency", "relevance")` hoisted to
a module constant `SCORING_SIGNALS`, same values, same use — so scores are
behaviourally identical to `e3f9e37`. Recorded because a floor calibrated
against an unknown in-flight scorer would be worthless, and "I checked" is not
evidence.

Separately: `path_score_distribution.py` serves fixtures from **this worktree**,
not from `~/Projects/corvus`, precisely because that checkout is mid-refactor.
Freezing a corpus while the ranking function moves underneath is the fixture
failure mode one level up — the receipt would still say FROZEN.

## 5. Where direction (a)–(d) actually stands

Unchanged and not re-derived: widening costs Bash one fire in 118 (−0.8%), and
gains 10 fires across 128 Edit/Write calls (7.8% vs Bash's 55.9%).

What this session adds is that **the ordering of the work is now forced**:

1. The instrument fix (§2) gates (a), (b) and (c). Ship it first, ship it
   alone, and let it run long enough to prove the Bash sub-rate is stable
   *before* the gate moves — otherwise the widening and the instrument change
   land together and neither is attributable.
2. Criterion #1's score distribution gates (b) specifically. Unmeasured.
3. (d) — keep the cap, record why — is the only direction that requires
   **none** of the above, and the evidence for it got stronger today, not
   weaker: PreToolUse is now 4.0x the next-best channel (was 2.5x) at 2.30
   injections per session. The upside on offer is 10 warnings per 128 edits of
   unknown value; the thing at risk is the only proven channel in the system.

No threshold shipped. No gate widened. `PRE_TOOL_TOP_K` and
`PRE_TOOL_MIN_SCORE` are untouched, as the record's review trigger requires.

## Next session — concrete handoff

The live hook the harness actually executes is
`/home/tylerbvogel/Projects/corvus/harness/claude-code/memory_inject_hook.py`
(`~/.claude/settings.json:39,57,70`). **Nothing in this worktree is live.**
Shipping requires landing on `main` in the primary checkout, which was off
limits this session.

1. **Land the instrument fix first** (§2): `tool` field in `_log_injection`'s
   record + `by_tool` sub-split in `injection_channel.reconstruct_history`,
   absent-`tool` read as Bash. Verify the pooled PreToolUse row is unchanged
   and `by_tool.Bash` reproduces 16.90% on the existing 1,923.
2. **Then run `path_score_distribution.py`** — on a quiet machine, against
   `pretooluse-reach@v1` (already on disk, already proven to serve). It needs
   ~1 GB free for the second backend; check `free -m` first and do not start it
   beside other agent sessions.
3. **Then decide (a)–(d)** with §1's table as the before, and re-take the after
   only from `GET /metrics/mind/injection-channels`.
4. If (d): the record can close on §1 + §5 without ever running step 2.

Two side findings worth their own records, neither blocking:

- **Fixture expiry is silent.** A fixture manifest pins `backend_git` and
  `scoring_env_sha256` but nothing that would have caught the alembic contract
  moving underneath it. `load_fixture()` should assert the dump's schema
  identity against the serving build and fail with "recapture", not with a
  startup traceback 90 seconds later.
- **The roadmap gate blocks `ToolSearch`.** `NON_MATERIAL_TOOLS` contains
  `tool_search`, but the tool arrives as `ToolSearch` → `toolsearch`, which
  matches neither that set nor `READ_TOOL_WORDS` (no separator before
  `search`), so it falls through to "unknown tools may mutate". A read-only
  schema lookup should not need admission.
