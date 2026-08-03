"""Give every HTTP request an identity that survives into its log lines.

Placed so it wraps the rest of the stack: a request that is rejected by the
access gate or blows up in another middleware still gets a correlated log and
a response header, which are exactly the requests hardest to diagnose without
one.

The inbound ``X-Request-ID`` is honoured when present so a caller can stitch
its own trace to ours, but it is length-capped and character-filtered — an
identifier is echoed back in a response header, and unvalidated header
reflection is how header injection works.
"""

from __future__ import annotations

import logging
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.context import bound, new_request_id


logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
SESSION_HEADER = "X-Corvus-Session"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _clean(value: str | None) -> str | None:
    if value and _SAFE_ID.fullmatch(value):
        return value
    return None


class CorrelationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, tenant_id: str = "") -> None:
        super().__init__(app)
        self._tenant_id = tenant_id

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _clean(request.headers.get(REQUEST_ID_HEADER)) or new_request_id()
        with bound(
            request_id=request_id,
            session_id=_clean(request.headers.get(SESSION_HEADER)),
            tenant=self._tenant_id or None,
            method=request.method,
            path=request.url.path,
        ):
            request.state.request_id = request_id
            try:
                response = await call_next(request)
            except Exception:
                # An unhandled exception escapes past this middleware to
                # Starlette's error layer, which is OUTSIDE it — so the 500 it
                # builds cannot carry our header. The log line is therefore the
                # only durable link between that failure and the request that
                # caused it, and it must be written here, still inside the
                # correlation scope. Found by a test asserting the opposite.
                logger.exception("unhandled exception", extra={
                    "event": "http.unhandled", "outcome": "error",
                })
                raise
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
