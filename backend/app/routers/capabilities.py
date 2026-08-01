"""Capability Capsule transport and compatibility API."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.capability_capsule import export_capsule, import_capsule, transfer_health, verify

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


class InspectRequest(BaseModel):
    capsule: dict
    target_harness: str = Field(min_length=1, max_length=50)


class ImportRequest(InspectRequest):
    apply: bool = False


@router.get("/capsule")
async def capsule(db: AsyncSession = Depends(get_db)):
    return await export_capsule(db)


@router.post("/verify")
async def verify_capsule(req: InspectRequest):
    result = verify(req.capsule)
    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/reconcile")
async def reconcile_capsule(req: ImportRequest, db: AsyncSession = Depends(get_db)):
    return await import_capsule(db, req.capsule, target_harness=req.target_harness,
                                apply=req.apply)


@router.post("/health")
async def capsule_health(req: InspectRequest):
    return transfer_health(req.capsule, req.target_harness)
