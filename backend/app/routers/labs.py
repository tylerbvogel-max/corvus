"""Read-only experimental surfaces imported through the Carlos Lab."""

from fastapi import APIRouter, Depends

from app.middleware.rbac import UserIdentity, require_role
from app.services.oracle_funnel_artifacts import latest_artifact

router = APIRouter(prefix="/admin/labs", tags=["admin-labs"])


@router.get("/oracle-funnel")
async def oracle_funnel_latest(
    _identity: UserIdentity = Depends(require_role("admin")),
):
    """Latest real Oracle Funnel artifact; never synthesizes demo results."""
    return latest_artifact()
