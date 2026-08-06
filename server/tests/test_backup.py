"""Unit tests for rivulets.backup — exercised against a real file-based
SQLite DB in a temp workspace dir, not the in-memory `client`/`db_session`
fixtures (VACUUM INTO and WAL semantics need an actual file on disk)."""

from collections.abc import AsyncIterator
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


async def test_create_backup_daily_is_idempotent_per_day(
    settings: Settings, engine: AsyncEngine
) -> None:
    first = await backup.create_backup(settings, engine, prefix=backup.DAILY_PREFIX)
    second = await backup.create_backup(settings, engine, prefix=backup.DAILY_PREFIX)
    assert first == second
    assert len(list(settings.backups_dir.glob(f"{backup.DAILY_PREFIX}*.db"))) == 1


async def test_create_backup_raises_on_failed_integrity_check(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _always_fails(_path: Path) -> bool:
        return False

    monkeypatch.setattr(backup, "_integrity_check", _always_fails)
    with pytest.raises(backup.BackupIntegrityError):
        await backup.create_backup(settings, engine, prefix=backup.MANUAL_PREFIX)
    # The bad file is left in place for inspection, not deleted.
    assert len(list(settings.backups_dir.glob(f"{backup.MANUAL_PREFIX}*.db"))) == 1


def test_prune_backups_keeps_only_most_recent(settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True)
    names = [f"{backup.DAILY_PREFIX}2026-01-0{n}.db" for n in range(1, 6)]
    for name in names:
        (settings.backups_dir / name).write_bytes(b"x")

    deleted = backup.prune_backups(settings, prefix=backup.DAILY_PREFIX, keep=3)

    remaining = sorted(p.name for p in settings.backups_dir.glob(f"{backup.DAILY_PREFIX}*.db"))
    assert remaining == names[-3:]
    assert {p.name for p in deleted} == set(names[:-3])


def test_list_backups_reports_kind(settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True)
    (settings.backups_dir / f"{backup.DAILY_PREFIX}2026-01-01.db").write_bytes(b"x")
    (settings.backups_dir / f"{backup.MANUAL_PREFIX}2026-01-01T000000Z.db").write_bytes(b"x")
    (settings.backups_dir / f"{backup.PRE_UPGRADE_PREFIX}0.1.0-2026-01-01T000000Z.db").write_bytes(
        b"x"
    )

    infos = backup.list_backups(settings)

    kinds = {info.filename: info.kind for info in infos}
    assert kinds[f"{backup.DAILY_PREFIX}2026-01-01.db"] == "daily"
    assert kinds[f"{backup.MANUAL_PREFIX}2026-01-01T000000Z.db"] == "manual"
    assert kinds[f"{backup.PRE_UPGRADE_PREFIX}0.1.0-2026-01-01T000000Z.db"] == "pre-upgrade"


def test_list_backups_empty_when_dir_missing(settings: Settings) -> None:
    assert backup.list_backups(settings) == []


def test_resolve_backup_path_rejects_traversal(settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True)
    real = settings.backups_dir / f"{backup.MANUAL_PREFIX}2026-01-01T000000Z.db"
    real.write_bytes(b"x")

    assert backup.resolve_backup_path(settings, real.name) == real
    assert backup.resolve_backup_path(settings, "../../etc/passwd") is None
    assert backup.resolve_backup_path(settings, "does-not-exist.db") is None


async def test_run_startup_backup_checks_first_run_writes_marker_no_pre_upgrade(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "APP_VERSION", "1.0.0")

    await backup.run_startup_backup_checks(settings, engine)

    assert (settings.backups_dir / backup.VERSION_MARKER_NAME).read_text() == "1.0.0"
    assert not list(settings.backups_dir.glob(f"{backup.PRE_UPGRADE_PREFIX}*.db"))
    assert list(settings.backups_dir.glob(f"{backup.DAILY_PREFIX}*.db"))


async def test_run_startup_backup_checks_snapshots_on_version_change(
    settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "APP_VERSION", "1.0.0")
    await backup.run_startup_backup_checks(settings, engine)

    monkeypatch.setattr(backup, "APP_VERSION", "2.0.0")
    await backup.run_startup_backup_checks(settings, engine)

    pre_upgrade = list(settings.backups_dir.glob(f"{backup.PRE_UPGRADE_PREFIX}*.db"))
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
