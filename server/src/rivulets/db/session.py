"""Async SQLAlchemy engine/session setup.

PRAGMAs: WAL mode, NORMAL sync (safe under WAL), a busy timeout to smooth
over concurrent writers, and a 64MB page cache.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

from rivulets.config import Settings, get_settings
from rivulets.db.base import Base

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA cache_size=-64000",
    "PRAGMA foreign_keys=ON",
    "PRAGMA mmap_size=268435456",
)


def _apply_pragmas(
    dbapi_connection: DBAPIConnection, _connection_record: ConnectionPoolEntry
) -> None:
    cursor = dbapi_connection.cursor()
    for pragma in _PRAGMAS:
        cursor.execute(pragma)
    cursor.close()


def make_engine(settings: Settings | None = None, *, in_memory: bool = False) -> AsyncEngine:
    """Create the async engine. `in_memory=True` is used by the test suite."""
    if in_memory:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        settings = settings or get_settings()
        settings.ensure_workspace_dirs()
        engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")

    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def override_engine(engine: AsyncEngine | None) -> None:
    """Swap the process-wide engine/session-factory singletons.

    Two callers: the test suite, which points every request at an isolated
    in-memory DB without a full dependency-injection rewrite of `get_db`;
    and rivulets.backup.restore_from_backup, which passes None after
    swapping in a restored db file so the next `get_engine()` call opens a
    fresh connection against it instead of reusing a handle to the
    pre-restore file."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = None


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create tables if they don't exist. Real app startup uses
    db/migrate.py's run_migrations() instead (#229) -- this stays as the
    fast, no-Alembic-machinery path for the test suite's in-memory DB."""
    engine = engine or get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session


async def begin_immediate(session: AsyncSession) -> None:
    """Commits whatever this session has pending, then opens a fresh
    transaction with SQLite's BEGIN IMMEDIATE instead of the default
    deferred BEGIN (#237) -- takes the write lock right away rather than
    lazily on first write, so a concurrent session doing the same thing
    blocks up front (subject to busy_timeout above) instead of both
    acquiring read locks and racing to upgrade, which SQLite resolves
    with an immediate SQLITE_BUSY instead of a wait.

    The commit-first step means this is safe to call as the first thing
    in a critical section regardless of what the session did earlier in
    the request -- it always starts that section from a clean, committed
    state. It only fails if a write already happened *after* the last
    commit on this same session (aiosqlite's driver only opens a real
    transaction on first write, not on read, so reads beforehand are
    fine) -- callers own not doing that.
    """
    await session.commit()
    conn = await session.connection()
    await conn.exec_driver_sql("BEGIN IMMEDIATE")
