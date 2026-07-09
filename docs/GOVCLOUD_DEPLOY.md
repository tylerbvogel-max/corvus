# Corvus GovCloud Deployment Guide

Corvus deploys as a backend knowledge service inside the GovCloud boundary (CMMC L2 / FedRAMP Medium). The tenant's LLM assistant (any OpenAI-compatible frontend) calls Corvus via REST API to get domain-enriched system prompts. No Corvus frontend is required.

## Prerequisites

| Requirement | Details |
|---|---|
| Azure Database for PostgreSQL | Flexible Server, GovCloud region, encryption at rest |
| Azure OpenAI resource | GPT-4o and GPT-4o-mini deployments minimum |
| Azure AD app registration | For RBAC JWT validation (optional, can use header mode) |
| Docker runtime | On the deployment host |
| Transfer media | Approved media for air-gap transfer (USB, optical, etc.) |

## Transfer Procedure

### On development machine:

```bash
# Build and package
cd ~/Projects/corvus
./scripts/package-airgap.sh
```

This creates `transfer-package/` containing:
- `corvus-govcloud.tar.gz` — Docker image
- `docker-compose.yml` + `docker-compose.govcloud.yml`
- `env.govcloud.example` — Environment template
- `SHA256SUMS` — Integrity manifest

Copy `transfer-package/` to approved transfer media.

### On GovCloud host:

```bash
# 1. Verify integrity
sha256sum -c SHA256SUMS

# 2. Load Docker image
docker load < corvus-govcloud.tar.gz

# 3. Configure environment
cp env.govcloud.example .env
# Edit .env with actual values (see below)

# 4. Run database migrations
docker run --rm --env-file .env corvus-govcloud:latest alembic upgrade head

# 5. Start Corvus
docker compose -f docker-compose.yml -f docker-compose.govcloud.yml up -d

# 6. Seed the knowledge graph (first deployment only)
curl -X POST http://localhost:8002/admin/seed \
  -H "Authorization: Bearer $CORVUS_ACCESS_KEY"

# 7. Verify
curl http://localhost:8002/admin/health-check \
  -H "Authorization: Bearer $CORVUS_ACCESS_KEY"
```

## Environment Configuration

Copy `.env.govcloud.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | Azure PostgreSQL connection string |
| `AZURE_OPENAI_API_KEY` | Yes | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Yes | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT_GPT4O` | Yes | GPT-4o deployment name |
| `AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI` | Yes | GPT-4o-mini deployment name |
| `LLM_MODEL_ALIASES` | Yes | `{"haiku":"azure-gpt4o-mini","sonnet":"azure-gpt4o","opus":"azure-gpt4o"}` |
| `CORVUS_ACCESS_KEY` | Yes | Shared access key (rotate via Key Vault) |
| `RBAC_MODE` | No | `disabled` (default), `header`, or `azure_ad` |
| `RBAC_AZURE_TENANT_ID` | If RBAC=azure_ad | Azure AD tenant ID |
| `TENANT_ID` | No | Default: `corvus-aero` |
| `PORT` | No | Default: `8002` |
| `CORS_ORIGINS` | No | Comma-separated allowed origins |

## Registering Tools with an LLM Assistant

Corvus exposes an OpenAI function-calling schema endpoint:

```bash
curl http://localhost:8002/admin/tool-definitions \
  -H "Authorization: Bearer $CORVUS_ACCESS_KEY"
```

This returns JSON that any OpenAI-compatible assistant can register as function-calling tools. Categories:
- **context** — Primary integration (`corvus_query_context`)
- **reporting** — Cost, health, governance dashboards
- **governance** — Proposal review, observation triage
- **ingestion** — Document parsing, observation submission
- **maintenance** — Seeding, pruning, integrity scans

## RBAC Roles

| Role | Access |
|---|---|
| `reader` | Query context, view reports, health checks |
| `reviewer` | Everything reader + approve/reject proposals, ingest documents |
| `admin` | Everything + seed, reset, prune, integrity scans |

## Verification Checklist

- [ ] `curl /admin/health-check` returns 200
- [ ] `curl /admin/tool-definitions` returns tool schemas
- [ ] `curl -X POST /context` with test query returns enriched prompt
- [ ] `curl /admin/cost-report` shows $0.00 (no queries yet)
- [ ] `curl /admin/compliance-audit` returns empty audit trail
- [ ] RBAC: reader role gets 403 on `/admin/seed`
- [ ] Audit log: POST requests appear in `audit_log` table
