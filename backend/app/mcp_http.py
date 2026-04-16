"""Remote MCP transport — Pattern GTM-B.

Exposes the stdio MCP server defined in :mod:`app.mcp_server` as a
remote HTTP/SSE endpoint at ``/mcp``. This lets external LLM frontends
(Cursor, Claude Enterprise, ChatGPT Enterprise, MCP-compatible
orchestrators) discover and invoke the same 7 Corvus tools over the
network that are available via the local stdio bridge.

Architecture
------------

* :data:`session_manager` is a module-level
  :class:`mcp.server.streamable_http_manager.StreamableHTTPSessionManager`
  bound to the existing :mod:`app.mcp_server.mcp` ``FastMCP`` instance.
  Stateless mode is used — every HTTP request creates a short-lived
  transport so multi-worker deploys don't need sticky sessions.

* :func:`mcp_lifespan` is an ``@asynccontextmanager`` the FastAPI app's
  lifespan calls to bring ``session_manager`` up and down. Required by
  the SDK: ``run()`` can only be called once per instance.

* :func:`mcp_asgi_endpoint` is a raw ASGI callable mounted by
  :mod:`app.main` at ``/mcp``. It enforces the same OAuth2/JWT
  contract as ``/v1/query`` by resolving identity from the request
  headers before delegating to the MCP session manager. Because the
  session manager writes the full HTTP response itself, this handler
  must be a pure ASGI callable — wrapping it in a FastAPI route that
  returns a :class:`Response` would double-send.

Stateless mode trade-off: long-running streaming tool calls that
outlive a single HTTP request aren't supported. The 7 tools in
``mcp_server.py`` all return in one round trip, so this is fine for
Phase 1.5.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import AsyncIterator

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from app.mcp_server import mcp
from app.middleware.rbac import ROLE_LEVELS, UserIdentity, resolve_identity

logger = logging.getLogger(__name__)


# Bind a stateless session manager to the existing FastMCP instance.
# The session manager owns transport lifecycle; the FastMCP wrapper
# owns tool registration (unchanged from stdio transport).
session_manager: StreamableHTTPSessionManager = StreamableHTTPSessionManager(
    app=mcp._mcp_server,
    event_store=None,
    stateless=True,
    json_response=False,
)


@contextlib.asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """Context manager to slot into :func:`app.main.lifespan`.

    Starts the session manager's anyio task group on enter and tears it
    down on exit. Required because ``session_manager.run()`` can only
    be invoked once per instance.
    """
    async with session_manager.run():
        logger.info("MCP streamable-http session manager started")
        yield
        logger.info("MCP streamable-http session manager stopping")


def _reader_identity_or_none(request: Request) -> UserIdentity | None:
    """Resolve the caller via RBAC; return ``None`` if unauthorized."""
    try:
        identity = resolve_identity(request)
    except Exception:
        return None
    if ROLE_LEVELS.get(identity.role, 0) < ROLE_LEVELS["reader"]:
        return None
    return identity


async def _send_unauthorized(send: Send, reason: str) -> None:
    """Emit a minimal 401 JSON response over raw ASGI."""
    body = json.dumps({"error": "unauthorized", "detail": reason}).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})


class MCPAsgiEndpoint:
    """Pure ASGI entry point mounted at ``/mcp``.

    Implemented as a class (not a function) so Starlette's
    :class:`Route` treats ``self.__call__`` as an ASGI app rather than
    wrapping it as a ``func(request) -> response`` handler.

    Authenticates via :func:`resolve_identity` and then hands the
    request off to :data:`session_manager`, which writes the full
    response itself.
    """

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send,
    ) -> None:
        assert scope["type"] == "http", "MCP endpoint only handles HTTP"
        request = Request(scope, receive=receive)
        identity = _reader_identity_or_none(request)
        if identity is None:
            await _send_unauthorized(send, "reader role required")
            return
        await session_manager.handle_request(scope, receive, send)


# Module-level singleton so ``app.main`` can reference it by attribute.
mcp_asgi_endpoint = MCPAsgiEndpoint()
