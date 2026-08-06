"""Local, node-scoped backup & restore for the workspace SQLite database.

Implements the DR strategy documented in
docs/infrastructure/security-and-dr.md: an automatic daily backup (7-day
retention), a pre-upgrade snapshot taken the first time a new binary
version starts against an existing workspace (5-snapshot retention),
on-demand manual backups, and a restore flow.

Every snapshot here uses `VACUUM INTO`, including for the pre-upgrade
case where the doc describes a raw `cp db db-wal db-shm` instead —
VACUUM INTO checkpoints the live DB into one self-contained file
regardless of whether the source is WAL-mode-on-disk or (as in tests)
:memory:, which is both simpler and safer than copying three files that
could be mid-write relative to each other.

Backups are node-local recovery artifacts and are never synced to peers:
this module has no dependency on rivulets.sync and never touches a
`workspace_settings` row (which *is* synced) — the pre-upgrade version
marker lives in a plain file inside backups_dir instead.
"""

import asyncio
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from rivulets.config import Settings
from rivulets.db.session import get_engine, override_engine
from rivulets.version import APP_VERSION

logger = logging.getLogger(__name__)

DAILY_PREFIX = "rivulets-"
MANUAL_PREFIX = "manual-"
PRE_UPGRADE_PREFIX = "pre-upgrade-v"

_DAILY_RETENTION = 7
_PRE_UPGRADE_RETENTION = 5
VERSION_MARKER_NAME = ".last_version"


class BackupIntegrityError(RuntimeError):
    """A freshly-written backup file failed its own `PRAGMA integrity_check`."""


@dataclass(frozen=True)
class BackupInfo:
    filename: str
    kind: str  # "daily" | "manual" | "pre-upgrade"
    size_bytes: int
    created_at: str  # ISO 8601 (UTC), from filesystem mtime


def _kind_of(filename: str) -> str:
    if filename.startswith(PRE_UPGRADE_PREFIX):
        return "pre-upgrade"
    if filename.startswith(MANUAL_PREFIX):
        return "manual"
    return "daily"


def _integrity_check(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    finally:
        conn.close()


async def _snapshot(engine: AsyncEngine, dest: Path) -> None:
    async with engine.connect() as conn:
        await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        await conn.exec_driver_sql("VACUUM INTO ?", (str(dest),))


async def create_backup(
    settings: Settings, engine: AsyncEngine, *, prefix: str = DAILY_PREFIX
) -> Path:
    """Snapshot the live database into `backups_dir`.

    Raises BackupIntegrityError (leaving the bad file in place for
    inspection, per the doc's "alert user if check fails") if the
    snapshot doesn't pass its own integrity check.
    """
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    if prefix == DAILY_PREFIX:
        # One file per calendar day: a second same-day call (e.g. a
        # same-day restart) overwrites it rather than piling up, which
        # gives us the doc's "first start of each day" trigger without a
        # separate has-today-run-already marker.
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    else:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    dest = settings.backups_dir / f"{prefix}{stamp}.db"
    dest.unlink(missing_ok=True)
    await _snapshot(engine, dest)
    if not await asyncio.to_thread(_integrity_check, dest):
        raise BackupIntegrityError(f"Backup at {dest} failed integrity check")
    return dest


def prune_backups(settings: Settings, *, prefix: str, keep: int) -> list[Path]:
    """Delete all but the `keep` most recent snapshots matching `prefix`.

    Filenames embed a sortable timestamp, so lexicographic sort matches
    chronological order. Returns the deleted paths.
    """
    candidates = sorted(settings.backups_dir.glob(f"{prefix}*.db"))
    stale = candidates[:-keep] if keep > 0 else candidates
    for path in stale:
        path.unlink(missing_ok=True)
    return stale


def list_backups(settings: Settings) -> list[BackupInfo]:
    if not settings.backups_dir.is_dir():
        return []
    infos = [
        BackupInfo(
            filename=path.name,
            kind=_kind_of(path.name),
            size_bytes=(stat := path.stat()).st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        )
        for path in settings.backups_dir.glob("*.db")
    ]
    return sorted(infos, key=lambda b: b.created_at, reverse=True)


async def run_startup_backup_checks(settings: Settings, engine: AsyncEngine) -> None:
    """Called once from app.py's lifespan, after tables exist. Covers both
    documented startup-time triggers: the pre-upgrade snapshot (version
    marker changed since the last start) and the daily snapshot (idempotent
    — see create_backup's same-day overwrite).

    A failed backup is logged, not raised — there's no UI notification
    channel yet to surface the doc's "alert user if check fails" through
    (see #27's follow-up), and refusing to start the app over a backup
    failure would make this reliability feature a new source of downtime.
    """
    try:
        await _pre_upgrade_backup_if_version_changed(settings, engine)
    except BackupIntegrityError:
        logger.exception("Pre-upgrade backup failed its integrity check")
    try:
        await create_backup(settings, engine, prefix=DAILY_PREFIX)
        prune_backups(settings, prefix=DAILY_PREFIX, keep=_DAILY_RETENTION)
    except BackupIntegrityError:
        logger.exception("Daily backup failed its integrity check")


async def _pre_upgrade_backup_if_version_changed(settings: Settings, engine: AsyncEngine) -> None:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    marker = settings.backups_dir / VERSION_MARKER_NAME
    last_version = marker.read_text().strip() if marker.exists() else None
    # None means a fresh workspace with nothing worth protecting yet, not a
    # version change — skip the snapshot but still record a marker so the
    # *next* start has something to compare against.
    if last_version is not None and last_version != APP_VERSION:
        await create_backup(settings, engine, prefix=f"{PRE_UPGRADE_PREFIX}{last_version}-")
        prune_backups(settings, prefix=PRE_UPGRADE_PREFIX, keep=_PRE_UPGRADE_RETENTION)
    marker.write_text(APP_VERSION)


def resolve_backup_path(settings: Settings, filename: str) -> Path | None:
    """Validate a user-supplied filename against `backups_dir`, rejecting
    path traversal (e.g. `../../etc/passwd`) or any reference outside it.
    Returns None if the resolved path doesn't exist or escapes backups_dir.
    """
    candidate = (settings.backups_dir / Path(filename).name).resolve()
    backups_dir = settings.backups_dir.resolve()
    if not candidate.is_relative_to(backups_dir) or not candidate.is_file():
        return None
    return candidate


async def restore_from_backup(settings: Settings, backup_path: Path) -> None:
    """Restore the workspace DB from a snapshot taken by create_backup.

    Disposes the current DB engine and clears the process-wide override so
    the next `get_engine()` call opens a fresh connection against the
    restored file — the doc's "stop, copy, delete WAL/SHM, start" restore
    procedure, without requiring an actual process restart.
    """
    await get_engine().dispose()
    for suffix in ("-wal", "-shm"):
        sidecar = settings.db_path.with_name(settings.db_path.name + suffix)
        sidecar.unlink(missing_ok=True)
    shutil.copyfile(backup_path, settings.db_path)
    override_engine(None)
