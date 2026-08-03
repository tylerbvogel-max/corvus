# Runbook: LLM provider failure

## Symptom

Jobs that need inference fail; recall and the rest of the API keep working.
Corvus routes inference through CLI subprocesses (Claude CLI, Codex CLI), not
API SDKs, so "provider down" usually means a missing or unauthenticated binary.

## Detection

- The failing job's receipt: `outcome: error` with the provider named
- `GET /metrics/mind/jobs` → that job `failing`
- `corvus-job-alert` fires with job identity, tenant, last success, exception
- **Not** visible in `/ready`: readiness is about serving, and Corvus serves
  recall without any provider at all

## First response

1. Read the receipt's exception — it names the provider and model.
2. Check the binaries resolve: `CLAUDE_CLI_PATH` (default: `claude` on PATH)
   and `CODEX_PATH` (default `~/.local/bin/codex`).
3. Check auth, not just presence: an installed CLI that is logged out fails the
   same way at call time.
4. Fallback chains are configured per model — a single provider being down
   should degrade, not stop, inference. If it stopped, the fallback map is
   wrong, not the provider.

## Drill — EXECUTED 2026-08-02

A backend was booted with `CLAUDE_CLI_PATH=/nonexistent/claude` and
`CODEX_PATH=/nonexistent/codex`, then a deep auditor pass was forced.

Observed:

```
POST /auditor/run?mode=deep   -> HTTP 500
receipt outcome: error
receipt exception: ValueError: Provider 'openai_codex' not configured for model 'codex-sol'.
```

Liveness and readiness both stayed **200** throughout, which is correct: the
service was serving, it simply could not think.

Also checked, and worth knowing: a janitor consolidation pass on the same
provider-less backend reported 415 pairs and 0 judged with no error. That is
**not** a silent provider failure — those pairs were already-judged content
that never entered the judging batch, so no provider call was attempted.
