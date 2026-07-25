# Codex harness

Codex 0.145.0 provides native command hooks for the same lifecycle events used
by Claude Code. `corvus_hook.py` is therefore only a compatibility adapter over
the existing hooks in `harness/claude-code/`; it contains no memory logic.

- Recall: the existing `mind_mcp_server.py` is registered as `corvus-mind`.
- Injection: `SessionStart`, `UserPromptSubmit`, and `PreToolUse` delegate to
  `memory_inject_hook.py` and `roadmap_gate_hook.py`. Codex adds their merged
  `additionalContext` as developer context, including the compiled charter,
  self-model, and deterministic roadmap admission policy at session start.
  Mapped-project mutations block until the session holds a revision-pinned
  admission or a logged off-ledger override.
- Capture: `PostToolUse` and `Stop` delegate to `episode_hook.py`. Codex supplies
  its rollout JSONL as `transcript_path`; that format is explicitly unstable,
  so the distiller should continue treating it as best-effort input. Stop also
  emits a planning return receipt after admitted material work.

Install `hooks.json` at `~/.codex/hooks.json`, then review and trust it with
`/hooks` in Codex. For vetted noninteractive acceptance runs only, Codex also
offers `--dangerously-bypass-hook-trust`.

`~/.codex/AGENTS.md` is not used for Corvus injection: native pre-turn hooks
provide ambient dynamic context. AGENTS.md is only the static fallback ceiling
if hooks are disabled or untrusted.

Compiled procedures now arrive through the canonical Capability Capsule build:
the graph compiles to `~/.corvus-mind/capabilities/skills/`, then projects the
same verified `SKILL.md` into `~/.codex/skills/`. Codex's declared semantic and
lifecycle capabilities live in `harness/profiles/codex.json`; run
`../parity_probe.py` against port 8005 to verify transfer coverage.
