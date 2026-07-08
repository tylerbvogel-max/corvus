from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

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
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
