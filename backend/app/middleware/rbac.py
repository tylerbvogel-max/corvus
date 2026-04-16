"""Role-based access control for Corvus API endpoints.

Three roles in ascending privilege: reader < reviewer < admin.
Three modes controlled by RBAC_MODE env var:
  - "disabled" (default): all requests pass, backward-compatible
  - "header": reads X-Corvus-Role header (dev/testing)
  - "azure_ad": validates Azure AD JWT, maps claims to roles

Usage as a FastAPI dependency:
    @router.post("/admin/seed")
    async def seed_database(..., _user=Depends(require_role("admin"))):
"""

import logging
from dataclasses import dataclass
from types import MappingProxyType

from fastapi import Depends, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


# ── Role hierarchy ──

ROLE_LEVELS = MappingProxyType({
    "reader": 0,
    "reviewer": 1,
    "admin": 2,
})


@dataclass(frozen=True)
class UserIdentity:
    """Resolved user identity from auth context."""

    user_id: str
    role: str
    source: str  # "header" | "azure_ad" | "disabled"


# ── Azure AD JWT validation ──

def _decode_azure_jwt(token: str) -> dict:
    """Decode and validate an Azure AD JWT token."""
    import jwt
    from jwt import PyJWKClient

    assert settings.rbac_azure_tenant_id, "RBAC_AZURE_TENANT_ID not configured"
    jwks_url = (
        f"https://login.microsoftonline.com/"
        f"{settings.rbac_azure_tenant_id}/discovery/v2.0/keys"
    )
    jwk_client = PyJWKClient(jwks_url, cache_keys=True)
    signing_key = jwk_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.rbac_azure_tenant_id,
        options={"verify_exp": True},
    )
    assert isinstance(claims, dict), "JWT claims must be a dict"
    return claims


def _role_from_claims(claims: dict) -> str:
    """Extract Corvus role from Azure AD JWT claims."""
    roles = claims.get("roles", [])
    if not isinstance(roles, list):
        roles = []
    if settings.rbac_admin_claim in roles:
        return "admin"
    if settings.rbac_reviewer_claim in roles:
        return "reviewer"
    return "reader"


# ── Resolve identity by mode ──

def _resolve_disabled(request: Request) -> UserIdentity:
    """Disabled mode: everyone is admin. Honors X-Corvus-User header when
    present so single-user dev setups can still record a meaningful
    reviewer/applied_by name on proposals and action-bus lineage."""
    user_id = request.headers.get("X-Corvus-User", "anonymous").strip() or "anonymous"
    return UserIdentity(user_id=user_id, role="admin", source="disabled")


def _resolve_header(request: Request) -> UserIdentity:
    """Header mode: require X-Corvus-Role header; 401 if absent.

    Previously this defaulted to ``admin`` when the header was missing,
    which defeated the point of header-mode gating on externally-reachable
    surfaces like ``/v1/query``. Missing/invalid role now rejects the
    request outright.
    """
    raw = request.headers.get("X-Corvus-Role")
    if raw is None:
        raise HTTPException(status_code=401, detail="X-Corvus-Role header required")
    role = raw.strip().lower()
    if role not in ROLE_LEVELS:
        raise HTTPException(status_code=401, detail=f"Unknown role: {raw!r}")
    user_id = request.headers.get("X-Corvus-User", "dev-user")
    return UserIdentity(user_id=user_id, role=role, source="header")


def _resolve_azure_ad(request: Request) -> UserIdentity:
    """Azure AD mode: validate JWT and extract role from claims."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")
    token = auth[7:]
    try:
        claims = _decode_azure_jwt(token)
    except Exception as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    role = _role_from_claims(claims)
    user_id = claims.get("preferred_username", claims.get("sub", "unknown"))
    return UserIdentity(user_id=str(user_id), role=role, source="azure_ad")


_MODE_RESOLVERS = MappingProxyType({
    "disabled": _resolve_disabled,
    "header": _resolve_header,
    "azure_ad": _resolve_azure_ad,
})


# ── Public API: dependency factory ──

def resolve_identity(request: Request) -> UserIdentity:
    """Resolve the current user identity based on RBAC_MODE."""
    mode = settings.rbac_mode
    resolver = _MODE_RESOLVERS.get(mode)
    assert resolver is not None, f"Unknown RBAC_MODE: {mode!r}"
    return resolver(request)


def require_role(minimum: str):
    """Return a FastAPI dependency that enforces a minimum role level.

    Example: Depends(require_role("reviewer"))
    """
    assert minimum in ROLE_LEVELS, f"Unknown role: {minimum!r}"
    min_level = ROLE_LEVELS[minimum]

    def _check(identity: UserIdentity = Depends(resolve_identity)) -> UserIdentity:
        user_level = ROLE_LEVELS.get(identity.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=f"Role {identity.role!r} insufficient; requires {minimum!r}",
            )
        return identity

    return _check
