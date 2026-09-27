from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


class SkillAwareSession(AsyncSession):
    """Refresh native skill projections after committed source changes.

    Action handlers add source IDs to ``info`` while their transaction is
    open. Disk projections only change after the database commit succeeds.
    The pending set survives a refresh failure so a later commit can retry.
    """

    async def commit(self) -> None:
        await super().commit()
        changed = sorted(self.info.get("skill_source_changed", ()))
        if not changed or self.info.get("skill_projection_refreshing"):
            return
        from app.services.skill_compiler import refresh_projections_after_reconsolidation
        self.info["skill_projection_refreshing"] = True
        try:
            result = await refresh_projections_after_reconsolidation(self, changed)
            if result.get("db_changed"):
                await super().commit()
            self.info.pop("skill_source_changed", None)
        finally:
            self.info.pop("skill_projection_refreshing", None)

    async def rollback(self) -> None:
        await super().rollback()
        self.info.pop("skill_source_changed", None)

# A real connection pool. This was NullPool ("fresh connections") for years,
# which meant EVERY session paid TCP+auth+setup and concurrent batches
# churned hundreds of connections — a proven contributor to the 30-220s
# stage-latency tails in batch telemetry (2026-07 efficiency audit).
# pool_pre_ping keeps the safety property NullPool was after: a dead/stale
# connection is detected and replaced before use, never handed to a request.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
)
async_session = async_sessionmaker(engine, class_=SkillAwareSession, expire_on_commit=False)


async def release_connection_before_external_io(db: AsyncSession) -> None:
    """Finish the current DB phase before waiting on slow external work.

    ``AsyncSession`` autobegins on the first query and keeps that transaction's
    pooled connection until commit/rollback.  Request-scoped sessions therefore
    must cross an explicit phase boundary before a provider subprocess or
    network judge can wait for seconds or minutes.

    Callers own the semantic boundary: any pending writes are committed.  The
    session remains reusable and, because the sessionmaker disables expiration
    on commit, already-loaded ORM state remains safe to read while the external
    operation runs.  A later DB operation transparently checks out a connection
    for the persistence phase.
    """
    await db.commit()


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
