# Corvus-Mind (the Pallium) — Functionality Health Check

Quick manual runbook to confirm the whole memory stack is alive.
Interim doc (2026-07-10) — a self-checking status surface in the UI is
the future elegant solution.

## 0. Services (30 seconds)

```bash
systemctl --user status corvus-mind.service --no-pager | head -3   # active (running)
systemctl --user list-timers 'corvus-mind*' --no-pager             # distill (30m) + janitor (6h) + compile (24h)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8005/docs # 200
```

Frontend dev UI (only if you want the pages): from `~/Projects/corvus/frontend`:
`VITE_API_PORT=8005 npm run dev` → http://localhost:8004/ (backend 8005; vite owns 8004).

## 1. Read path — recall works, costs $0

```bash
curl -s -X POST http://localhost:8005/recall -H "Content-Type: application/json" \
  -d '{"query":"which port does the corvus mind backend use?","top_k":3,"persist":false}'
```

Expect: JSON with `hits` (port lesson near the top), `latency_ms` under ~200 warm.
First call after a restart is slow (~10s) — the embedding model loads lazily.

## 2. Hooks — capture + injection live in Claude Code

- `python3 -c "import json; print(sorted(json.load(open('/home/tylerbvogel/.claude/settings.json'))['hooks']))"`
  → `['PostToolUse', 'PreToolUse', 'SessionStart', 'Stop', 'UserPromptSubmit']`
- In any Claude Code session, run one command, then:
  `tail -2 ~/.corvus-mind/episodes/<session-id>.jsonl` → recent PostToolUse events.
- Injection proof: ask Claude something port-flavored ("which port for a new
  corvus tenant?") — a `Corvus-Mind recalled memories` block should precede its answer,
  and the episode log gains an `"event": "Injection"` line.

## 3. Write path — remember → recall round trip

```bash
curl -s -X POST http://localhost:8005/remember -H "Content-Type: application/json" \
  -d '{"lesson":"healthcheck probe","evidence":"manual check [session:healthcheck]","label":"healthcheck probe lesson","scope":"Environment"}'
```

Expect `"route": "auto"` + a `neuron_id`. Recall it back, then tidy up in the
Explorer (deactivate) or let decay reclaim it.

## 4. Frontend pages (http://localhost:8004/, memory tenant nav)

| Page | Healthy looks like |
|---|---|
| **Memory** (landing) | Stat tiles populated; trust sparklines; spend > $0; stage timings |
| **Sessions** | Rows for live + `bf-*` sessions; distilled column shows ✓ or `queued` |
| **Inbox** | Open findings/proposals/borderline pairs (or "self-resolving") |
| **Skills** | `mind-*` entries, all sources `healthy`, SKILL.md renders |
| **Explorer** | Scopes → roles → **project containers** → lessons |
| **3D Universe** | Violet-caged skill nodes (dark matter), idle drift, click → content card |
| **Performance / Pipeline Timing** | Recall traffic in stage stats |

A transient 500 on any page usually = backend restarting (~10s); confirm with
`systemctl --user status corvus-mind` before digging.

## 5. Background organs (after a few hours)

```bash
curl -s http://localhost:8005/distill/status        # ready count drains over time
curl -s http://localhost:8005/janitor/status        # absorbed_lessons grows as dups fuse
curl -s http://localhost:8005/compile/status        # clusters shrink as skills emit
ls ~/.claude/skills/                                # mind-* dirs accumulate
tail -3 ~/.corvus-mind/episodes/janitor-actions.jsonl
```

## 6. New-session persistence (nothing to do)

Hooks + MCP registration are user-level (`~/.claude/settings.json`,
`claude mcp add --scope user`) and the backend is a boot-persistent systemd
service — every new Claude Code session gets capture, injection, `recall`/
`remember` MCP tools, and any compiled `mind-*` skills automatically.
