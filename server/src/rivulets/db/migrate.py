"""Runs real Alembic migrations against the workspace DB at startup (#229).

`db/session.py`'s `init_db()` (plain `Base.metadata.create_all`) stays as
the fast path for tests and the in-memory DB -- it never touches an
existing table's columns, which is exactly why it can't carry a real
deployment through an upgrade. `run_migrations()` is what app.py's
lifespan actually calls: it runs the Alembic revision chain rooted at
`db/migrations/versions/0001_baseline.py`, which reconciles a fresh,
partial, or fully up-to-date database with `Base.metadata` alike.

Uses Alembic's connection-sharing pattern (see `migrations/env.py`) via
`AsyncConnection.run_sync` rather than opening a second, separate
connection to the same SQLite file with a sync engine -- migrations run on
the same connection the rest of app startup uses.
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from rivulets.db.session import get_engine


def _migrations_dir() -> Path:
    # Same sys._MEIPASS idiom as app.py's _static_dir: PyInstaller's
    # onefile build extracts bundled `datas` under a temp dir at that
    # path, not the source tree's actual location.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "rivulets" / "db" / "migrations"
    return Path(__file__).resolve().parent / "migrations"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_migrations_dir()))
    return cfg


def _upgrade_head(connection: Connection, cfg: Config) -> None:
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def run_migrations(engine: AsyncEngine | None = None) -> None:
    """Bring the workspace DB's schema up to `Base.metadata`, in place."""
    engine = engine or get_engine()
    cfg = _alembic_config()
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade_head, cfg)
