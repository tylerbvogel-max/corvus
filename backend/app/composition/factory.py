"""The one documented way to build a Corvus application.

``create_app`` is the only place a FastAPI instance is constructed. Everything
it mounts, warms, or starts comes from the resolved capability profile, and
every capability-owned module is imported *here*, lazily, at build time —
never at ``app.main`` import. That is what makes absence provable: a disabled
capability's modules are missing from ``sys.modules`` entirely, so its provider
clients and module-level state cannot have been constructed.

The MCP transport is the clearest case. ``app.mcp_server`` builds a ``FastMCP``
instance and registers every tool at import; ``app.mcp_http`` builds a session
manager at import. Both are reached only through EXTERNAL_API here, so a
profile without it never constructs either.
"""

from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.composition.capabilities import Capability
from app.composition.profiles import CapabilityProfile, resolve_profile
from app.config import settings
from app.database import async_session, engine
from app.middleware.access_gate import AccessGateMiddleware
from app.middleware.audit import AuditMiddleware
from app.middleware.correlation import CorrelationMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.models import Neuron, SystemState
from app.observability.json_logging import configure_logging
from app.services.readiness import evaluate_readiness
from app.tenant import tenant as default_tenant

logger = logging.getLogger(__name__)

# API prefixes the SPA catch-all must never swallow. Kept as the union across
# all capabilities on purpose: a disabled capability's path must 404 as "no
# such route", not silently render the frontend shell, which would read to a
# client as though the endpoint existed and returned HTML.
_API_PREFIXES = (
    "/neurons", "/queries", "/query", "/context", "/eval-scores", "/admin",
    "/health", "/tenant", "/tenants", "/docs", "/openapi", "/ingest", "/models",
    "/chat", "/learning-analytics", "/roadmap-ledgers", "/v1", "/mcp",
    "/recall", "/remember", "/distill", "/janitor", "/compile", "/auditor",
    "/metrics", "/capabilities", "/engrams", "/lineage",
)


def _load(module: str, attr: str):
    """Import ``module`` and return ``attr``, with a composition-aware error."""
    try:
        loaded = importlib.import_module(module)
    except ImportError as exc:
        raise RuntimeError(
            f"capability profile references {module!r}, which failed to "
            f"import: {exc}"
        ) from exc
    try:
        return getattr(loaded, attr)
    except AttributeError:
        raise RuntimeError(
            f"capability profile references {module}:{attr}, which does not exist"
        ) from None


def _build_lifespan(profile: CapabilityProfile):
    """Compose the startup sequence this profile actually requires."""
    steps = profile.startup_steps()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        skipped = [s.name for s in _all_steps() if s not in steps]
        logger.info(
            "composition: tenant=%s capabilities=%s startup_steps=%s skipped=%s",
            profile.tenant_id, ",".join(profile.names),
            ",".join(s.name for s in steps), ",".join(skipped) or "none",
        )

        locked = [s for s in steps if s.needs_canonical_lock]
        unlocked_before = [
            s for s in steps
            if not s.needs_canonical_lock and (not locked or _index(s) < _index(locked[0]))
        ]
        unlocked_after = [
            s for s in steps
            if not s.needs_canonical_lock and s not in unlocked_before
        ]

        for step in unlocked_before:
            await _run_step(step)

        if locked:
            # Seed mutations stay inside the advisory lock exactly as before.
            from app.composition.startup import canonical_startup_lock
            async with canonical_startup_lock(engine):
                for step in locked:
                    await _run_step(step)

        for step in unlocked_after:
            await _run_step(step)

        if Capability.EXTERNAL_API in profile:
            from app.mcp_http import mcp_lifespan
            async with mcp_lifespan():
                yield
        else:
            yield

    return lifespan


def _all_steps():
    from app.composition.capabilities import STARTUP_STEPS
    return STARTUP_STEPS


def _index(step) -> int:
    return _all_steps().index(step)


async def _run_step(step) -> None:
    """Import and run one startup step, awaiting it if it is a coroutine."""
    import inspect
    fn = _load(step.module, step.attr)
    # validate_schema_authority is the one step that takes the engine.
    result = fn(engine) if step.name == "validate_schema_authority" else fn()
    if inspect.isawaitable(result):
        await result


def create_app(profile: CapabilityProfile | None = None, tenant=None) -> FastAPI:
    """Build the application for ``profile`` (default: the active tenant's).

    Passing an explicit profile is how tests compose a tenant that does not
    exist on disk, and how the negative tests prove a disabled capability is
    absent rather than merely refused.
    """
    tenant = tenant or default_tenant
    profile = profile or resolve_profile(tenant)

    # Before anything else logs: uvicorn and this module both emit during
    # construction, and a line written before configuration lands unstructured.
    configure_logging(settings.log_level)

    app = FastAPI(
        title=tenant.display_name,
        description=tenant.description,
        version="0.1.0",
        lifespan=_build_lifespan(profile),
    )
    # Read by /admin/architecture and the composition tests; the resolved
    # profile is part of the app's identity, not ambient state.
    app.state.capability_profile = profile

    _add_middleware(app, tenant_id=tenant.tenant_id)
    _add_error_contract(app)

    # Record what actually mounted, so readiness can prove the profile's claims
    # rather than restate them. A capability that is granted but whose router
    # never mounted is the AC-8 banner failure class, and it is invisible
    # unless the two sets are compared.
    mounted_specs: set[str] = set()
    for spec in profile.routers():
        app.include_router(_load(spec.module, spec.attr))
        mounted_specs.add(str(spec))
    app.state.mounted_router_specs = mounted_specs

    if Capability.EXTERNAL_API in profile:
        _mount_mcp(app)

    _add_core_routes(app, tenant, profile)
    _mount_frontend(app)
    return app


def _add_middleware(app: FastAPI, tenant_id: str = "") -> None:
    cors_origins = (
        [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        if settings.cors_origins
        else ["http://localhost:5173", f"http://localhost:{settings.port}"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Security headers middleware — defense-in-depth headers on all responses
    # Addresses: NIST 800-53 AC-12/SC-10/SC-28, CMMC 3.1.11/3.13.9, SOC 2 CC6.1
    app.add_middleware(SecurityHeadersMiddleware)
    # Access gate middleware — shared key authentication when CORVUS_ACCESS_KEY is set
    app.add_middleware(AccessGateMiddleware)
    # Audit logging middleware — logs all POST/PUT/DELETE/PATCH to audit_log table
    # Addresses: NIST 800-53 AU-2/AU-3/AU-12, CMMC 3.3.1, SOC 2 CC7.2
    app.add_middleware(AuditMiddleware)
    # Correlation LAST, so it is OUTERMOST: Starlette runs the most recently
    # added middleware first. A request rejected by the access gate above is
    # precisely the one that is hardest to diagnose without an id, so it must
    # already be inside the correlation scope by the time the gate sees it.
    app.add_middleware(CorrelationMiddleware, tenant_id=tenant_id)


def _add_error_contract(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Catch all unhandled exceptions and return sanitized JSON error.

        SI-11: Error messages must not reveal system implementation details.
        """
        if isinstance(exc, HTTPException):
            raise exc
        status = 504 if "timed out" in str(exc).lower() else 500
        # Log the real error server-side, return generic message to client
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method, request.url.path, exc, exc_info=True,
        )
        return JSONResponse(status_code=status, content={"detail": "Internal server error"})


def _mount_mcp(app: FastAPI) -> None:
    """Mount the remote MCP transport at /mcp.

    Mount as raw ASGI — the MCP session manager writes the full HTTP
    response itself, so we cannot use a request-handler-style route
    (would double-send). ``app.mount`` binds the path prefix to the ASGI
    app directly without any request wrapping. Mount's path regex only
    matches ``/mcp/`` (trailing slash + suffix), so we add a method-
    agnostic 307 redirect from ``/mcp`` → ``/mcp/`` for MCP clients that
    configure the bare URL.
    """
    from app.mcp_http import mcp_asgi_endpoint

    app.mount("/mcp", mcp_asgi_endpoint)

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def _mcp_slash_redirect() -> RedirectResponse:
        """Preserve method (307) when forwarding bare ``/mcp`` to ``/mcp/``."""
        return RedirectResponse(url="/mcp/", status_code=307)


def _add_core_routes(app: FastAPI, tenant, profile: CapabilityProfile) -> None:
    """Routes present in every profile, whatever else is switched off."""

    @app.get("/tenant")
    async def get_tenant():
        """Return tenant configuration for the frontend."""
        return {
            "tenant_id": tenant.tenant_id,
            "display_name": tenant.display_name,
            "description": tenant.description,
            "seed_prompts": tenant.seed_prompts,
            "memory_surface": Capability.MEMORY in profile,
            # Verification #6: the frontend must not advertise navigation for a
            # capability this backend did not compose.
            "capabilities": list(profile.names),
            "disabled_capabilities": list(profile.disabled),
        }

    @app.get("/tenants")
    async def list_tenants():
        """Return all available tenants with their default URLs."""
        import yaml as _yaml

        tenants_dir = Path(__file__).resolve().parents[2] / "tenants"
        result = []
        for td in sorted(tenants_dir.iterdir()):
            yaml_path = td / "tenant.yaml"
            if not td.is_dir() or not yaml_path.exists():
                continue
            with open(yaml_path) as f:
                cfg = _yaml.safe_load(f)
            result.append({
                "tenant_id": td.name,
                "display_name": cfg.get("display_name", td.name),
                "default_port": cfg.get("default_port"),
            })
        return result

    @app.get("/health")
    async def health():
        """LIVENESS. Can this process answer at all?

        Deliberately touches NO dependency. This used to query the database and
        report neuron counts, which meant a Postgres blip looked like a dead
        process and invited a pointless restart. Dependency state lives at
        /ready; the counts moved there, where a query is legitimate.
        """
        return {"status": "ok", "check": "liveness", "tenant": tenant.tenant_id}

    @app.get("/ready")
    async def ready(response: Response):
        """READINESS. Are this build's dependencies satisfied?

        Returns 503 when they are not, so a load balancer or deploy gate can
        act on it. Reports every check even after one fails — an operator
        debugging a bad deploy needs the whole picture at once.
        """
        report = await evaluate_readiness(
            engine=engine,
            tenant=tenant.tenant_id,
            granted=profile.names,
            claimed_specs={str(spec) for spec in profile.routers()},
            mounted_specs=getattr(app.state, "mounted_router_specs", set()),
        )
        payload = report.as_dict()
        if not report.ready:
            response.status_code = 503
            return payload

        # Informational only, and only once the database is known reachable:
        # these are the counts /health used to carry.
        async with async_session() as db:
            neuron_count = (await db.execute(select(func.count(Neuron.id)))).scalar() or 0
            state = (await db.execute(
                select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
        payload["neuron_count"] = neuron_count
        payload["total_queries"] = state.total_queries if state else 0
        return payload


def _mount_frontend(app: FastAPI) -> None:
    """Serve frontend static files if built."""
    frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if not frontend_dist.exists():
        return

    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _is_api_path(path: str) -> bool:
        return bool(path) and any(path.startswith(p.lstrip("/")) for p in _API_PREFIXES)

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the SPA frontend, falling back to index.html for client-side routes."""
        # Serve real static files first (logos, images, etc.)
        if full_path:
            file_path = frontend_dist / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
        # Never intercept API paths — let them 404 naturally
        if _is_api_path(full_path):
            raise HTTPException(status_code=404, detail="Not found")
        # SPA fallback
        return FileResponse(str(frontend_dist / "index.html"))
