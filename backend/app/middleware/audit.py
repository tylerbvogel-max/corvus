"""Best-effort, awaited audit records for selected mutation requests.

Body summaries retain bounded shape, never arbitrary keys or scalar values.
Reads, excluded paths and downstream exceptions are not recorded here.
See docs/audit-logging.md for coverage, privacy and failure limitations.
"""

import json
import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.database import async_session
from app.models import AuditLog

logger = logging.getLogger(__name__)

# Endpoints to skip (high-frequency, non-security-relevant)
SKIP_ENDPOINTS = {
    "/corvus/frame",       # Screen captures — too frequent, logged separately
    "/health",             # Health check
    "/corvus/latest-frame", # Frame retrieval
}

# Fixed resource budgets, not empirically tuned throughput targets. These bound
# summary work/storage, not request.body() buffering or the application's uploads.
MAX_BODY_SUMMARY = 2000
MAX_BODY_PARSE_BYTES = 64 * 1024
MAX_BODY_DEPTH = 8
MAX_BODY_NODES = 256

# Fields to redact from request body summaries
REDACT_FIELDS = {
    "password", "secret", "token", "api_key", "apikey", "authorization",
    "access_token", "refresh_token", "client_secret", "private_key",
    "cookie", "set_cookie",
}


def _reject_json_constant(value: str) -> None:
    """Reject Python's nonstandard NaN/Infinity JSON extension without logging it."""
    raise ValueError("nonstandard JSON constant")


def _redact_body(body_bytes: bytes) -> str:
    """Return bounded JSON metadata/shape; never fall back to request text.

Secret fields become fixed redaction markers at every visited depth. Other keys
become positional labels and scalar values become type markers: a denylist alone
cannot make free-form memory, credentials or even field names safe to persist.
Limits omit entire summaries or subtrees, never slice serialized JSON.
"""
    if not body_bytes:
        return ""

    def omitted(reason: str) -> str:
        return json.dumps({"bytes": len(body_bytes), "omitted": reason})

    if len(body_bytes) > MAX_BODY_PARSE_BYTES:
        return omitted("body_size_limit")
    try:
        data = json.loads(body_bytes.decode("utf-8"), parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        return omitted("invalid_json")
    if not isinstance(data, (dict, list)):
        return omitted("non_container_json")

    remaining = MAX_BODY_NODES

    def shape(value, depth: int = 0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise ValueError("node_limit")
        if depth >= MAX_BODY_DEPTH:
            return "[depth limit]"
        if isinstance(value, dict):
            result = {}
            for index, (key, child) in enumerate(value.items()):
                normalized = key.lower().replace("-", "_")
                if normalized in REDACT_FIELDS:
                    remaining -= 1
                    if remaining < 0:
                        raise ValueError("node_limit")
                    result[normalized] = "[REDACTED]"
                else:
                    result[f"field_{index}"] = shape(child, depth + 1)
            return result
        if isinstance(value, list):
            return [shape(child, depth + 1) for child in value]
        if value is None:
            return "[null]"
        if isinstance(value, bool):
            return "[boolean]"
        if isinstance(value, (int, float)):
            return "[number]"
        return "[string]"

    try:
        summary = json.dumps({"bytes": len(body_bytes), "body": shape(data)})
    except ValueError:
        return omitted("node_limit")
    if len(summary.encode("utf-8")) > MAX_BODY_SUMMARY:
        return omitted("summary_size_limit")
    return summary


class AuditMiddleware(BaseHTTPMiddleware):
    """Log state-changing requests to the audit_log table."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only log state-changing methods
        if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
            return await call_next(request)

        # Skip high-frequency endpoints
        path = request.url.path
        if path in SKIP_ENDPOINTS:
            return await call_next(request)

        # Read request body for summary (must cache for downstream handlers)
        body_bytes = await request.body()
        body_summary = _redact_body(body_bytes)

        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        status_code = response.status_code
        error_detail = None
        if status_code >= 400:
            error_detail = f"HTTP {status_code}"

        # Async database I/O is awaited before returning the response. This is
        # best-effort persistence, not a background task or a durable queue.
        try:
            async with async_session() as db:
                record = AuditLog(
                    action=request.method,
                    endpoint=path[:500],
                    status_code=status_code,
                    user_agent=user_agent,
                    client_ip=client_ip,
                    request_body_summary=body_summary if body_summary else None,
                    response_time_ms=elapsed_ms,
                    error_detail=error_detail,
                )
                db.add(record)
                await db.commit()
        except Exception:
            # Exception messages/tracebacks may contain SQL parameters or raw
            # request metadata. Emit only a fixed diagnostic; preserve response.
            logger.error("Audit log write failed; record not persisted")

        return response
