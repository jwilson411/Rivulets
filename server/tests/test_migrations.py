"""Unit tests for rivulets.db.migrate — exercised against real file-based
SQLite DBs (not the in-memory `client`/`db_session` fixtures), including a
synthetic "old workspace" fixture standing in for #229's checked-in-DB ask:
a real 0.3-era file isn't available, but a hand-written legacy `agent`
table missing the columns the issue calls out (fallback_models,
approved_for_unattended_tools, output_schema) exercises exactly the same
"ALTER an existing table" path a real one would."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from rivulets.app import create_app
from rivulets.config import Settings
from rivulets.db.migrate import run_migrations
from rivulets.db.models import Agent
from rivulets.db.session import get_engine, make_engine, override_engine, session_scope
from rivulets.sync.engine import SyncEngine, reset_sync_engine_for_testing

_LEGACY_SCHEMA_SQL = """
CREATE TABLE workspace (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    vector_clock INTEGER NOT NULL
);

CREATE TABLE human (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    vector_clock INTEGER NOT NULL
);

-- Old-shaped `agent`: missing fallback_models (#103),
-- approved_for_unattended_tools (#100), and output_schema (#107) — every
-- other column matches today's rivulets.db.models.Agent exactly.
CREATE TABLE agent (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    instructions TEXT NOT NULL,
    model TEXT NOT NULL,
    agentos_agent_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    vector_clock INTEGER NOT NULL,
    CONSTRAINT idx_agent_name UNIQUE (name)
);

INSERT INTO agent (
    id, name, description, instructions, model, agentos_agent_id,
    created_at, updated_at, vector_clock
) VALUES (
    'legacy-agent-1', 'Old Agent', 'a pre-existing agent', 'be helpful',
    'anthropic:claude-haiku-4-5-20251001', NULL,
    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0
);
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(workspace_dir=tmp_path / "workspace")
    s.ensure_workspace_dirs()
    return s


def _sqlite_columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _sqlite_tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}
    finally:
        conn.close()


async def test_run_migrations_on_fresh_db_creates_full_schema(settings: Settings) -> None:
    engine = make_engine(settings)
    try:
        await run_migrations(engine)
    finally:
        await engine.dispose()

    tables = _sqlite_tables(settings.db_path)
    assert {"workspace", "agent", "workflow", "knowledge_base"} <= tables
    assert "fallback_models" in _sqlite_columns(settings.db_path, "agent")
    assert "error_message" in _sqlite_columns(settings.db_path, "run_span")


async def test_run_migrations_upgrades_legacy_schema(settings: Settings) -> None:
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(_LEGACY_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

    engine = make_engine(settings)
    try:
        await run_migrations(engine)
    finally:
        await engine.dispose()

    agent_columns = _sqlite_columns(settings.db_path, "agent")
    assert {"fallback_models", "approved_for_unattended_tools", "output_schema"} <= agent_columns

    # A table this legacy workspace never had at all also gets created.
    assert "workflow" in _sqlite_tables(settings.db_path)

    # The pre-existing row survives, with the new NOT NULL column
    # backfilled to its ORM-level default rather than left null or the
    # migration erroring out.
    conn = sqlite3.connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT name, approved_for_unattended_tools, fallback_models, output_schema "
            "FROM agent WHERE id = 'legacy-agent-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("Old Agent", 0, None, None)


async def test_run_migrations_adds_workspace_singleton_constraint(settings: Settings) -> None:
    """#324: a workspace already sitting at 0005 (before Workspace.singleton
    existed, see _LEGACY_SCHEMA_SQL's `workspace` table above) gets the
    column and its UNIQUE constraint backfilled by 0006 -- and, once
    backfilled, a second `workspace` row is rejected at the DB layer
    instead of silently succeeding."""
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(_LEGACY_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO workspace (id, name, key_hash, created_at, updated_at, vector_clock) "
            "VALUES ('legacy-workspace-1', 'My Workspace', 'hash', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    engine = make_engine(settings)
    try:
        await run_migrations(engine)
    finally:
        await engine.dispose()

    assert "singleton" in _sqlite_columns(settings.db_path, "workspace")

    conn = sqlite3.connect(settings.db_path)
    try:
        # The pre-existing row survived the rebuild, backfilled to the
        # ORM-level default rather than left null.
        row = conn.execute(
            "SELECT singleton FROM workspace WHERE id = 'legacy-workspace-1'"
        ).fetchone()
        assert row == (1,)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspace "
                "(id, singleton, name, key_hash, created_at, updated_at, vector_clock) "
                "VALUES ('legacy-workspace-2', 1, 'My Workspace', 'hash', "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)"
            )
    finally:
        conn.close()


async def test_run_migrations_is_idempotent(settings: Settings) -> None:
    engine = make_engine(settings)
    try:
        await run_migrations(engine)
        await run_migrations(engine)  # a second app start against the same file
    finally:
        await engine.dispose()

    assert "fallback_models" in _sqlite_columns(settings.db_path, "agent")


async def _noop_async(*_args: object, **_kwargs: object) -> None:
    return None


async def test_app_boots_against_legacy_workspace(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot test #229 asks for: start the real app (create_app() and
    its actual lifespan, migrations included, not a monkeypatched-out
    fast path) against the legacy fixture, then query Agent through the
    ORM to confirm the exact failure mode the issue describes --
    `OperationalError: no such column` on a pre-existing table's new
    column -- no longer happens."""
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(_LEGACY_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(SyncEngine, "start", _noop_async)
    monkeypatch.setattr(SyncEngine, "stop", _noop_async)
    monkeypatch.setattr("rivulets.app.run_startup_backup_checks", _noop_async)
    monkeypatch.setattr("rivulets.app.run_scheduler_loop", _noop_async)
    monkeypatch.setattr("rivulets.app.run_retention_loop", _noop_async)
    reset_sync_engine_for_testing()

    override_engine(make_engine(settings))
    try:
        app = create_app()
        with TestClient(app):
            async with session_scope() as db:
                result = await db.execute(select(Agent).where(Agent.id == "legacy-agent-1"))
                agent = result.scalar_one()
                assert agent.approved_for_unattended_tools is False
                assert agent.fallback_models is None
                assert agent.output_schema is None
    finally:
        await get_engine().dispose()
        override_engine(None)
        reset_sync_engine_for_testing()
