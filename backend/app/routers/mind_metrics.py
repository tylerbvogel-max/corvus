"""Memory-organ metrics endpoint (the tailored Evaluate surface).

GET /metrics/mind — performance (recall latency + stages, injection
coverage, distiller funnel, janitor/compiler activity, cost ledger) and
growth (lesson corpus + daily timeseries). Read-only, no LLM.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.recall import require_memory_surface
from app.services.mind_metrics import collect_all

router = APIRouter(prefix="/metrics", tags=["memory"],
                   dependencies=[Depends(require_memory_surface)])


@router.get("/mind")
async def mind_metrics(db: AsyncSession = Depends(get_db)):
    """Aggregate memory-organ performance and growth metrics."""
    report = await collect_all(db)
    assert isinstance(report, dict), "metrics report must be a dict"
    return report
