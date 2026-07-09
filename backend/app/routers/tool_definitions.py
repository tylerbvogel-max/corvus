"""GET /admin/tool-definitions — OpenAI function-calling schemas.

Transforms FastAPI's auto-generated OpenAPI spec into OpenAI tool definitions
that any OpenAI-compatible assistant can register as function-calling tools.
"""

import re
from types import MappingProxyType

from fastapi import APIRouter, Depends, Request

from app.middleware.rbac import require_role

router = APIRouter(prefix="/admin", tags=["tools"])


# ── Tool catalog: which endpoints to expose and how ──
# Key: "METHOD /path" → category + assistant-oriented description.
# Paths use OpenAPI format ({param}) not FastAPI format.

_TOOL_CATALOG = MappingProxyType({
    # ── Context (primary integration) ──
    "POST /context": {
        "category": "context",
        "name": "corvus_query_context",
        "description": (
            "Ask Corvus a domain question and get a scored, sourced system prompt. "
            "Use this whenever a user asks a domain-specific question and you want "
            "Corvus knowledge to enrich your response."
        ),
    },
    "GET /neurons/tree": {
        "category": "context",
        "name": "corvus_neuron_tree",
        "description": "Browse the organizational knowledge hierarchy (departments, roles, layers).",
    },
    # ── Reporting ──
    "GET /admin/cost-report": {
        "category": "reporting",
        "name": "corvus_cost_report",
        "description": "Get token usage and cost breakdown across all queries. Use when asked about costs or usage.",
    },
    "GET /admin/scoring-health": {
        "category": "reporting",
        "name": "corvus_scoring_health",
        "description": "Check health of the 6-signal scoring pipeline. Use when diagnosing quality issues.",
    },
    "GET /admin/health-check": {
        "category": "reporting",
        "name": "corvus_health_check",
        "description": "System diagnostics: DB connectivity, neuron counts, circuit breaker status, drift detection.",
    },
    "GET /admin/alerts": {
        "category": "reporting",
        "name": "corvus_alerts",
        "description": "List active system alerts with severity levels. Use to check for issues.",
    },
    "GET /query/queries": {
        "category": "reporting",
        "name": "corvus_query_history",
        "description": "List recent queries with departments, neuron counts, and costs.",
    },
    "GET /query/learning-analytics": {
        "category": "reporting",
        "name": "corvus_learning_analytics",
        "description": "Synaptic learning stats: edge adjustments, promotions, demotions.",
    },
    "GET /admin/governance-dashboard": {
        "category": "reporting",
        "name": "corvus_governance_dashboard",
        "description": "High-level governance overview: proposal counts, approval rates, active users.",
    },
    "GET /admin/compliance-audit": {
        "category": "reporting",
        "name": "corvus_compliance_audit",
        "description": "Full audit trail of all graph modifications with provenance chains.",
    },
    "GET /proposals/stats": {
        "category": "reporting",
        "name": "corvus_proposal_stats",
        "description": "Proposal queue counts by state (proposed, approved, rejected, applied).",
    },
    # ── Governance ──
    "GET /proposals/": {
        "category": "governance",
        "name": "corvus_list_proposals",
        "description": "List pending proposals for review. Use before batch-review.",
    },
    "GET /proposals/{proposal_id}": {
        "category": "governance",
        "name": "corvus_proposal_detail",
        "description": "Get full detail of a specific proposal including evidence and items.",
    },
    "POST /proposals/{proposal_id}/review": {
        "category": "governance",
        "name": "corvus_review_proposal",
        "description": "Approve or reject a proposal. Use after reviewing proposal details.",
    },
    "POST /proposals/{proposal_id}/apply": {
        "category": "governance",
        "name": "corvus_apply_proposal",
        "description": "Apply an approved proposal to the live knowledge graph.",
    },
    "GET /ingest/observations": {
        "category": "governance",
        "name": "corvus_list_observations",
        "description": "List queued observations awaiting triage.",
    },
    "POST /ingest/observations/{obs_id}/approve": {
        "category": "governance",
        "name": "corvus_approve_observation",
        "description": "Approve a queued observation and create a neuron from it.",
    },
    "POST /ingest/observations/{obs_id}/reject": {
        "category": "governance",
        "name": "corvus_reject_observation",
        "description": "Reject a queued observation.",
    },
    "POST /query/{query_id}/rate": {
        "category": "governance",
        "name": "corvus_rate_query",
        "description": "Rate a query response (1-5). Drives the Impact scoring signal.",
    },
    # ── Ingestion ──
    "POST /admin/ingest-source": {
        "category": "ingestion",
        "name": "corvus_ingest_source",
        "description": (
            "Parse a document into neuron proposals. Provide source_text, citation, "
            "and source_type. Returns proposals for human review."
        ),
    },
    "POST /ingest/observation": {
        "category": "ingestion",
        "name": "corvus_ingest_observation",
        "description": "Submit an individual knowledge observation for classification and ingestion.",
    },
    # ── Maintenance ──
    "POST /admin/seed": {
        "category": "maintenance",
        "name": "corvus_seed_database",
        "description": "Seed the neuron graph with initial data. Use only on first deployment or reset.",
    },
    "POST /admin/reset": {
        "category": "maintenance",
        "name": "corvus_reset_firings",
        "description": "Clear all firing history and co-firing edges. Keeps neuron definitions. Destructive.",
    },
    "POST /admin/prune-edges": {
        "category": "maintenance",
        "name": "corvus_prune_edges",
        "description": "Remove stale low-weight co-firing edges from the graph.",
    },
    "POST /admin/alerts/{alert_id}/acknowledge": {
        "category": "maintenance",
        "name": "corvus_acknowledge_alert",
        "description": "Dismiss a specific system alert.",
    },
    "POST /admin/checkpoint": {
        "category": "maintenance",
        "name": "corvus_checkpoint",
        "description": "Export all neurons to a JSON checkpoint file.",
    },
    "POST /integrity/homeostasis/scan": {
        "category": "maintenance",
        "name": "corvus_homeostasis_scan",
        "description": "Scan for anomalous neuron activity patterns and signal drift.",
    },
    "POST /integrity/duplicates/scan": {
        "category": "maintenance",
        "name": "corvus_duplicate_scan",
        "description": "Detect duplicate neurons via semantic similarity.",
    },
})


def _method_and_path_from_openapi(path_key: str, method: str) -> str:
    """Build 'METHOD /path' key matching _TOOL_CATALOG format."""
    return f"{method.upper()} {path_key}"


def _build_parameters_schema(operation: dict, components: dict) -> dict:
    """Extract JSON Schema parameters from an OpenAPI operation."""
    params = {}
    required_params = []

    # Path/query parameters
    for param in operation.get("parameters", []):
        schema = param.get("schema", {"type": "string"})
        params[param["name"]] = schema
        if param.get("required", False):
            required_params.append(param["name"])

    # Request body
    body = operation.get("requestBody", {})
    body_content = body.get("content", {}).get("application/json", {})
    body_schema = body_content.get("schema", {})

    # Resolve $ref if present
    if "$ref" in body_schema:
        ref_path = body_schema["$ref"].replace("#/components/schemas/", "")
        body_schema = components.get("schemas", {}).get(ref_path, {})

    if body_schema:
        # Merge body properties into params
        for prop_name, prop_schema in body_schema.get("properties", {}).items():
            params[prop_name] = prop_schema
        for req in body_schema.get("required", []):
            if req not in required_params:
                required_params.append(req)

    result = {"type": "object", "properties": params}
    if required_params:
        result["required"] = required_params
    return result


@router.get("/tool-definitions")
async def get_tool_definitions(
    request: Request,
    category: str = "all",
    _user=Depends(require_role("reader")),
):
    """Return OpenAI function-calling tool definitions for external assistants.

    Query params:
      - category: "all", "context", "reporting", "governance", "ingestion", "maintenance"
    """
    assert hasattr(request, "app"), "request must have app attribute"
    openapi_spec = request.app.openapi()
    components = openapi_spec.get("components", {})
    tools = []

    for path_key, path_item in openapi_spec.get("paths", {}).items():
        for method in ("get", "post", "put", "delete", "patch"):
            if method not in path_item:
                continue
            catalog_key = _method_and_path_from_openapi(path_key, method)
            entry = _TOOL_CATALOG.get(catalog_key)
            if not entry:
                continue
            if category != "all" and entry["category"] != category:
                continue

            operation = path_item[method]
            parameters = _build_parameters_schema(operation, components)

            tools.append({
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "description": entry["description"],
                    "parameters": parameters,
                },
                "category": entry["category"],
                "endpoint": {"method": method.upper(), "path": path_key},
            })

    return {"tools": tools, "count": len(tools)}
