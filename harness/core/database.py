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

if settings.database_url.startswith("sqlite"):
    # SQLite allows exactly one writer at a time. With NullPool (a fresh
    # real connection per checkout, needed for the event-loop-binding reason
    # above) and this project's fire-and-forget background session runner
    # (harness/orchestrator/task_manager.py's `_run`, plus concurrent API
    # requests), it's normal for two connections to briefly want to write at
    # the same moment. Without a busy timeout, sqlite3's default is to raise
    # "database is locked" immediately rather than wait -- this sets each
    # new DBAPI connection to retry for up to 15s before giving up, which is
    # the standard fix for this exact situation and costs nothing when there
    # is no contention (a real request returns in milliseconds either way;
    # this only ever matters on the rare tick where two writers actually
    # collide). Postgres (the real-deployment backend) has proper MVCC and
    # never needs this. 15s (not the more common 5s) because this project's
    # own test suite runs 15+ concurrent sessions with fire-and-forget
    # background writers sharing one SQLite file -- a worst case a real
    # single-agent-at-a-time demo will never approach.
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_busy_timeout(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout = 15000")
        cursor.close()
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
