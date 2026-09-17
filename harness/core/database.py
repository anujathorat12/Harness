from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from harness.core.config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite"):
        # sqlite+aiosqlite:///./data/harness.db -> ./data/harness.db
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

# SQLite (via aiosqlite) ties each physical connection to the event loop
# that opened it -- test suites in particular can create/tear down event
# loops per test, which would otherwise leak connections bound to a closed
# loop. NullPool means every checkout opens a fresh connection and every
# checkin closes it, which sidesteps that entirely; it's also simply correct
# for a single-file SQLite database, which gets no real benefit from
# connection pooling anyway. Postgres deployments (DATABASE_URL pointing at
# postgresql+asyncpg://...) keep SQLAlchemy's normal pool.
_engine_kwargs: dict = {"echo": False, "future": True}
if settings.database_url.startswith("sqlite"):
    from sqlalchemy.pool import NullPool

    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create tables if they don't exist. A real production deployment would
    use Alembic migrations; for this scope, create-all is documented as the
    migration strategy (see README limitations)."""
    async with engine.begin() as conn:
        from harness.domain import models  # noqa: F401  (register metadata)
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
