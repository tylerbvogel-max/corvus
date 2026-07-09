# TLS Readiness Audit (fwd-tier1, 2026-07-09)

Audit of `backend/` for plaintext-HTTP assumptions and TLS gaps. Verdict:
**the codebase is TLS-ready as configuration** — no code changes required;
what remains is a per-deployment checklist.

## Already in place (verified)

| Item | Where | Status |
|---|---|---|
| HSTS on every response | `app/middleware/security_headers.py` (`Strict-Transport-Security: max-age=31536000; includeSubDomains`, SC-8) | ✅ |
| Security headers (CSP, nosniff, frame, referrer) | same middleware | ✅ |
| External API calls over HTTPS | eCFR (`services/ecfr_client.py`), Azure OpenAI endpoint (config) | ✅ |
| No hardcoded plaintext external endpoints | grep of `app/` — only loopback (below) | ✅ |
| CORS origins config-driven | `CORS_ORIGINS` env (`app/main.py`) | ✅ |
| DB URL fully overridable | `DATABASE_URL` env (`app/config.py`) | ✅ |
| LLM transport | Claude CLI subprocess (its own HTTPS) | ✅ |

## Accepted plaintext (loopback only, never leaves the host)

- Compliance self-scan providers call `http://localhost:{port}` — the app
  probing itself (`app/compliance/providers/*.py`). No change needed.
- CORS defaults (`http://localhost:5173`, `http://localhost:{port}`) apply
  only when `CORS_ORIGINS` is unset — a dev posture.

## Deployment checklist (config, not code)

1. **Terminate TLS in front of uvicorn.** The app serves plaintext HTTP by
   design; Render (demo) and a GovCloud ALB/nginx terminate TLS at the edge.
   For direct termination, uvicorn supports `--ssl-keyfile/--ssl-certfile`.
2. **Set `CORS_ORIGINS`** to the real `https://` origins in any non-local
   deployment (unset ⇒ localhost dev origins).
3. **Set `DATABASE_URL` with `?ssl=require`** (asyncpg) for any non-local
   Postgres. Local deployments use the loopback socket.
4. **Only use `CORVUS_ACCESS_KEY` behind TLS** — it is a shared bearer-style
   header and must never cross a plaintext hop.
5. **MCP HTTP transport** (`app/mcp_http.py`) rides the same app/proxy, so
   items 1–4 cover it.
