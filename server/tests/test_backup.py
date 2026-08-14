"""Unit tests for rivulets.backup — exercised against a real file-based
SQLite DB in a temp workspace dir, not the in-memory `client`/`db_session`
fixtures (VACUUM INTO and WAL semantics need an actual file on disk).

Deliberately doesn't touch AgentOS/sync_agents() — restore_from_backup
itself has no dependency on either (see its docstring); that composition
is exercised end-to-end in test_backups_api.py instead, against the real
`client` fixture that already initializes AgentOS."""

import sqlite3
import tarfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from rivulets import backup
from rivulets.config import Settings
from rivulets.db.models import WorkspaceSetting
from rivulets.db.session import (
    get_engine,
    init_db,
    make_engine,
    override_engine,
    session_scope,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(workspace_dir=tmp_path / "workspace")


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(settings)
    await init_db(eng)
    yield eng
    await eng.dispose()


async def test_create_backup_writes_valid_snapshot(settings: Settings, engine: AsyncEngine) -> None:
    path = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)
    assert path.exists()
    assert path.parent == settings.backups_dir
    assert path.name.startswith(backup.MANUAL_PREFIX)
    with tarfile.open(path) as archive:
        assert "./rivulets.db" in archive.getnames()


async def test_create_backup_includes_credentials_db_when_present(
    settings: Settings, engine: AsyncEngine
) -> None:
    settings.credential_fallback_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.credential_fallback_db_path)
    conn.execute("CREATE TABLE provider_keys (ref TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    path = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

    with tarfile.open(path) as archive:
        assert "./credentials.db" in archive.getnames()


async def test_create_backup_omits_credentials_db_when_absent(
    settings: Settings, engine: AsyncEngine
) -> None:
    path = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

    with tarfile.open(path) as archive:
        assert "./credentials.db" not in archive.getnames()


async def test_create_backup_includes_sync_node_identity(
    settings: Settings, engine: AsyncEngine
) -> None:
    settings.sync_dir.mkdir(parents=True, exist_ok=True)
    (settings.sync_dir / "node_key").write_bytes(b"x" * 32)

    path = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

    with tarfile.open(path) as archive:
        assert "./sync/node_key" in archive.getnames()


async def test_create_backup_includes_custom_tool_source(
    settings: Settings, engine: AsyncEngine
) -> None:
    settings.tools_dir.mkdir(parents=True, exist_ok=True)
    (settings.tools_dir / "greet.py").write_text("# a custom tool\n")

    path = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

    with tarfile.open(path) as archive:
        assert "./tools/greet.py" in archive.getnames()


async def test_create_backup_daily_is_idempotent_per_day(
    settings: Settings, engine: AsyncEngine
) -> None:
    first = await backup.create_backup(settings, engine, prefix=backup.DAILY_PREFIX)
    second = await backup.create_backup(settings, engine, prefix=backup.DAILY_PREFIX)
    assert first == second
    assert len(list(settings.backups_dir.glob(f"{backup.DAILY_PREFIX}*.tar"))) == 1


async def test_create_backup_raises_on_failed_integrity_check(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _always_fails(_path: Path) -> bool:
        return False

    monkeypatch.setattr(backup, "_integrity_check", _always_fails)
    with pytest.raises(backup.BackupIntegrityError):
        await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)
    # The bad file is left in place for inspection, not deleted.
    assert len(list(settings.backups_dir.glob(f"{backup.MANUAL_PREFIX}*.tar"))) == 1


def test_prune_backups_keeps_only_most_recent(settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True)
    names = [f"{backup.DAILY_PREFIX}2026-01-0{n}.tar" for n in range(1, 6)]
    for name in names:
        (settings.backups_dir / name).write_bytes(b"x")

    deleted = backup.prune_backups(settings, prefix=backup.DAILY_PREFIX, keep=3)

    remaining = sorted(p.name for p in settings.backups_dir.glob(f"{backup.DAILY_PREFIX}*.tar"))
    assert remaining == names[-3:]
    assert {p.name for p in deleted} == set(names[:-3])


def test_list_backups_reports_kind(settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True)
    (settings.backups_dir / f"{backup.DAILY_PREFIX}2026-01-01.tar").write_bytes(b"x")
    (settings.backups_dir / f"{backup.MANUAL_PREFIX}2026-01-01T000000Z.tar").write_bytes(b"x")
    (settings.backups_dir / f"{backup.PRE_UPGRADE_PREFIX}0.1.0-2026-01-01T000000Z.tar").write_bytes(
        b"x"
    )
    (settings.backups_dir / f"{backup.PRE_RESTORE_PREFIX}2026-01-01T000000Z.tar").write_bytes(b"x")

    infos = backup.list_backups(settings)

    kinds = {info.filename: info.kind for info in infos}
    assert kinds[f"{backup.DAILY_PREFIX}2026-01-01.tar"] == "daily"
    assert kinds[f"{backup.MANUAL_PREFIX}2026-01-01T000000Z.tar"] == "manual"
    assert kinds[f"{backup.PRE_UPGRADE_PREFIX}0.1.0-2026-01-01T000000Z.tar"] == "pre-upgrade"
    assert kinds[f"{backup.PRE_RESTORE_PREFIX}2026-01-01T000000Z.tar"] == "pre-restore"


def test_list_backups_empty_when_dir_missing(settings: Settings) -> None:
    assert backup.list_backups(settings) == []


def test_resolve_backup_path_rejects_traversal(settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True)
    real = settings.backups_dir / f"{backup.MANUAL_PREFIX}2026-01-01T000000Z.tar"
    real.write_bytes(b"x")

    assert backup.resolve_backup_path(settings, real.name) == real
    assert backup.resolve_backup_path(settings, "../../etc/passwd") is None
    assert backup.resolve_backup_path(settings, "does-not-exist.tar") is None


async def test_run_startup_backup_checks_first_run_writes_marker_no_pre_upgrade(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "APP_VERSION", "1.0.0")

    await backup.run_startup_backup_checks(settings, engine)

    assert (settings.backups_dir / backup.VERSION_MARKER_NAME).read_text() == "1.0.0"
    assert not list(settings.backups_dir.glob(f"{backup.PRE_UPGRADE_PREFIX}*.tar"))
    assert list(settings.backups_dir.glob(f"{backup.DAILY_PREFIX}*.tar"))


async def test_run_startup_backup_checks_snapshots_on_version_change(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "APP_VERSION", "1.0.0")
    await backup.run_startup_backup_checks(settings, engine)

    monkeypatch.setattr(backup, "APP_VERSION", "2.0.0")
    await backup.run_startup_backup_checks(settings, engine)

    pre_upgrade = list(settings.backups_dir.glob(f"{backup.PRE_UPGRADE_PREFIX}*.tar"))
    assert len(pre_upgrade) == 1
    assert pre_upgrade[0].name.startswith(f"{backup.PRE_UPGRADE_PREFIX}1.0.0-")
    assert (settings.backups_dir / backup.VERSION_MARKER_NAME).read_text() == "2.0.0"


async def test_restore_from_backup_reverts_to_snapshot_content(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # restore_from_backup only disposes the current engine and clears the
    # override so the *next* get_engine() call rebuilds one — in the real
    # app that rebuild uses the same get_settings() the caller already
    # passed in, but this test's `settings` fixture is a one-off Settings
    # distinct from the process-wide lru_cache'd one, so make_engine()'s
    # fallback needs to be pointed at it explicitly for the rebuild to
    # land on the same (now-restored) file.
    monkeypatch.setattr("rivulets.db.session.get_settings", lambda: settings)
    override_engine(engine)
    try:
        async with session_scope() as db:
            db.add(WorkspaceSetting(key="k", value='"before"'))
            await db.commit()

        snapshot = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

        async with session_scope() as db:
            row = await db.get(WorkspaceSetting, "k")
            assert row is not None
            row.value = '"after"'
            await db.commit()

        await backup.restore_from_backup(settings, snapshot)

        async with session_scope() as db:
            result = await db.execute(select(WorkspaceSetting).where(WorkspaceSetting.key == "k"))
            restored = result.scalar_one()
            assert restored.value == '"before"'
    finally:
        await get_engine().dispose()
        override_engine(None)


async def test_restore_from_backup_takes_pre_restore_safety_snapshot(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.db.session.get_settings", lambda: settings)
    override_engine(engine)
    try:
        snapshot = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

        await backup.restore_from_backup(settings, snapshot)

        pre_restore = list(settings.backups_dir.glob(f"{backup.PRE_RESTORE_PREFIX}*.tar"))
        assert len(pre_restore) == 1
        assert backup.list_backups(settings)[0].kind == "pre-restore"
    finally:
        await get_engine().dispose()
        override_engine(None)


async def test_restore_from_backup_pairs_credentials_db_with_snapshot(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup taken while credentials.db existed must restore it; one
    taken before it existed must remove any credentials.db that was
    created *after* that snapshot — otherwise a restore can leave
    provider_config.api_key_ref rows pointing at a fallback-store state
    that was never true at any single point in time (#243)."""
    monkeypatch.setattr("rivulets.db.session.get_settings", lambda: settings)
    override_engine(engine)

    # create_backup's non-daily filenames only carry second-level
    # precision (backup.py's own docstring) — two real-clock calls this
    # close together in the same test could collide on the exact same
    # filename and silently overwrite each other. Freeze/advance the clock
    # explicitly instead of relying on wall time not colliding.
    class _FakeDateTime(datetime):
        frozen_now = datetime(2026, 1, 1, tzinfo=UTC)

        @classmethod
        def now(cls, tz: object = None) -> datetime:  # noqa: ARG003
            return cls.frozen_now

    monkeypatch.setattr(backup, "datetime", _FakeDateTime)

    try:
        snapshot_without_creds = await backup.create_backup(
            settings, engine, prefix=backup.MANUAL_PREFIX
        )

        settings.credential_fallback_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.credential_fallback_db_path)
        conn.execute("CREATE TABLE provider_keys (ref TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO provider_keys VALUES ('provider-key:abc')")
        conn.commit()
        conn.close()

        _FakeDateTime.frozen_now += timedelta(seconds=2)
        snapshot_with_creds = await backup.create_backup(
            settings, engine, prefix=backup.MANUAL_PREFIX
        )

        await backup.restore_from_backup(settings, snapshot_without_creds)
        assert not settings.credential_fallback_db_path.exists()

        await backup.restore_from_backup(settings, snapshot_with_creds)
        assert settings.credential_fallback_db_path.exists()
        conn = sqlite3.connect(settings.credential_fallback_db_path)
        rows = conn.execute("SELECT ref FROM provider_keys").fetchall()
        conn.close()
        assert rows == [("provider-key:abc",)]
    finally:
        await get_engine().dispose()
        override_engine(None)


async def test_restore_from_backup_pairs_tool_source_with_snapshot(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#290: a backup taken while a custom tool's source file existed must
    restore it (even if the live file was later lost/mutated), and a
    restore must also clear any tool file created *after* that snapshot
    so tools_dir stays 1:1 with the Tool rows that came back in
    rivulets.db instead of mixing two points in time."""
    monkeypatch.setattr("rivulets.db.session.get_settings", lambda: settings)
    override_engine(engine)
    try:
        settings.tools_dir.mkdir(parents=True, exist_ok=True)
        (settings.tools_dir / "greet.py").write_text("# v1")
        snapshot = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

        # Mutated after the snapshot, plus an entirely new file with no
        # matching row at snapshot time.
        (settings.tools_dir / "greet.py").write_text("# mutated after snapshot")
        (settings.tools_dir / "post_snapshot_tool.py").write_text("# should not survive restore")

        await backup.restore_from_backup(settings, snapshot)

        assert (settings.tools_dir / "greet.py").read_text() == "# v1"
        assert not (settings.tools_dir / "post_snapshot_tool.py").exists()
    finally:
        await get_engine().dispose()
        override_engine(None)


async def test_restore_from_backup_wipes_agentos_db(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.db.session.get_settings", lambda: settings)
    override_engine(engine)
    try:
        settings.agentos_db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.agentos_db_path.write_bytes(b"stale agentos state")
        snapshot = await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)

        await backup.restore_from_backup(settings, snapshot)

        assert not settings.agentos_db_path.exists()
    finally:
        await get_engine().dispose()
        override_engine(None)
