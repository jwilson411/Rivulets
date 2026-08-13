"""Local, node-scoped backup & restore for the workspace.

Implements the DR strategy documented in
docs/infrastructure/security-and-dr.md: an automatic daily backup (7-day
retention), a pre-upgrade snapshot taken the first time a new binary
version starts against an existing workspace (5-snapshot retention),
on-demand manual backups, a pre-restore safety snapshot, and a restore
flow.

A backup is a tar archive (not a bare `.db` file, despite the module's
history — see git blame) bundling everything a restore needs to bring a
workspace back to a *consistent* state (#243), not just the app database:

- `rivulets.db` — the main app database, via `VACUUM INTO`.
- `credentials.db` — the encrypted provider-key fallback (security/
  credential_fallback.py), included whenever it exists so a restore can't
  leave `provider_config.api_key_ref` rows pointing at secrets that
  disagree with (or no longer exist in) the fallback store.
- `sync/` — this node's libp2p identity + capability tags (sync/identity.py,
  sync/capabilities.py). Small, per-installation files; included for DR
  completeness onto replacement hardware. Restoring them onto the *same*
  running node is a no-op in practice (they're created once and never
  change) and doesn't retroactively change an already-started SyncEngine's
  in-memory identity — see restore_from_backup's docstring.

Deliberately NOT included: `files/` (user-uploaded attachments) and
`agentos.db` (AgentOS's own run/session history — agentos/service.py).
Both are addressed differently rather than bundled into every automatic
daily/pre-upgrade snapshot:

- `files/` can be arbitrarily large (unlike the handful of small SQLite
  files above), which would make every automatic backup's cost scale with
  total attachment volume instead of app-state size. Out of scope for
  this module; the UI's backup panel discloses this so owners can cover
  it with regular filesystem backup practices instead.
- `agentos.db` is wiped and rebuilt from the restored `rivulets.db` via
  `sync_agents()` instead of being restored — see restore_from_backup's
  docstring.

Every SQLite file staged into the archive uses `VACUUM INTO`, including
for the pre-upgrade case where the doc describes a raw `cp db db-wal
db-shm` instead — VACUUM INTO checkpoints the live DB into one
self-contained file regardless of whether the source is WAL-mode-on-disk
or (as in tests) :memory:, which is both simpler and safer than copying
three files that could be mid-write relative to each other.

Backups are node-local recovery artifacts and are never synced to peers:
this module has no dependency on rivulets.sync (only copying the small
node-identity *files* sync/identity.py and sync/capabilities.py already
wrote) and never touches a `workspace_settings` row (which *is* synced)
— the pre-upgrade version marker lives in a plain file inside
backups_dir instead.
"""

import asyncio
import logging
import shutil
import sqlite3
import tarfile
import tempfile
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
PRE_RESTORE_PREFIX = "pre-restore-"

_DAILY_RETENTION = 7
_PRE_UPGRADE_RETENTION = 5
VERSION_MARKER_NAME = ".last_version"

_ARCHIVE_DB_NAME = "rivulets.db"
_ARCHIVE_CREDENTIALS_NAME = "credentials.db"
_ARCHIVE_SYNC_DIRNAME = "sync"


class BackupIntegrityError(RuntimeError):
    """A freshly-written backup file failed its own `PRAGMA integrity_check`."""


@dataclass(frozen=True)
class BackupInfo:
    filename: str
    kind: str  # "daily" | "manual" | "pre-upgrade" | "pre-restore"
    size_bytes: int
    created_at: str  # ISO 8601 (UTC), from filesystem mtime


def _kind_of(filename: str) -> str:
    if filename.startswith(PRE_UPGRADE_PREFIX):
        return "pre-upgrade"
    if filename.startswith(PRE_RESTORE_PREFIX):
        return "pre-restore"
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


def _vacuum_file_into(src: Path, dest: Path) -> None:
    """Sync counterpart of `_snapshot` for a plain sqlite3 file (no
    AsyncEngine of its own) — credential_fallback.py's `credentials.db`."""
    conn = sqlite3.connect(src)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()


async def _build_backup_archive(settings: Settings, engine: AsyncEngine, dest: Path) -> bool:
    """Stage a full workspace snapshot (module docstring's file list) in a
    temp dir and tar it into `dest`. Returns whether every embedded
    SQLite file passed its own integrity check.

    The archive is always written, even when a check fails — create_backup
    below decides whether to raise, but the doc's "leave the bad file in
    place for inspection" contract needs the file to actually exist.
    """
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        db_dest = tmp_path / _ARCHIVE_DB_NAME
        await _snapshot(engine, db_dest)
        ok = ok and await asyncio.to_thread(_integrity_check, db_dest)

        if settings.credential_fallback_db_path.is_file():
            creds_dest = tmp_path / _ARCHIVE_CREDENTIALS_NAME
            await asyncio.to_thread(
                _vacuum_file_into, settings.credential_fallback_db_path, creds_dest
            )
            ok = ok and await asyncio.to_thread(_integrity_check, creds_dest)

        if settings.sync_dir.is_dir():
            sync_dest = tmp_path / _ARCHIVE_SYNC_DIRNAME
            sync_dest.mkdir()
            for item in settings.sync_dir.iterdir():
                if item.is_file():
                    shutil.copyfile(item, sync_dest / item.name)

        with tarfile.open(dest, "w") as archive:
            archive.add(tmp_path, arcname=".")

    return ok


async def create_backup(
    settings: Settings, engine: AsyncEngine, *, prefix: str = DAILY_PREFIX
) -> Path:
    """Snapshot the live workspace (rivulets.db + companions — see module
    docstring) into `backups_dir` as a single tar archive.

    Raises BackupIntegrityError (leaving the bad file in place for
    inspection, per the doc's "alert user if check fails") if any embedded
    SQLite file doesn't pass its own integrity check.
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
    dest = settings.backups_dir / f"{prefix}{stamp}.tar"
    dest.unlink(missing_ok=True)
    ok = await _build_backup_archive(settings, engine, dest)
    if not ok:
        raise BackupIntegrityError(f"Backup at {dest} failed integrity check")
    return dest


def prune_backups(settings: Settings, *, prefix: str, keep: int) -> list[Path]:
    """Delete all but the `keep` most recent snapshots matching `prefix`.

    Filenames embed a sortable timestamp, so lexicographic sort matches
    chronological order. Returns the deleted paths.
    """
    candidates = sorted(settings.backups_dir.glob(f"{prefix}*.tar"))
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
        for path in settings.backups_dir.glob("*.tar")
    ]
    return sorted(infos, key=lambda b: b.created_at, reverse=True)


async def run_startup_backup_checks(settings: Settings, engine: AsyncEngine) -> None:
    """Called once from app.py's lifespan, *before* migrations run (#229) so
    the pre-upgrade snapshot below captures the true pre-migration file —
    VACUUM INTO works on a table-less or even not-yet-created DB file just
    fine, so there's no ordering requirement the other way. Covers both
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


def _unlink_with_sidecars(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


async def restore_from_backup(settings: Settings, backup_path: Path) -> None:
    """Restore the workspace from a snapshot archive taken by create_backup.

    Takes its own pre-restore safety snapshot of the *current* live state
    first, tagged PRE_RESTORE_PREFIX so it lists like any other manual
    backup — api/backups.py's restore endpoint is otherwise a one-click
    clobber with no way back. A failed safety snapshot is logged, not
    raised: refusing an owner's restore of a corrupted DB just because the
    *pre*-restore snapshot of that same corrupted DB failed its own
    integrity check would defeat the point of having a restore path.

    Swaps in `rivulets.db` and, if present in the archive, `credentials.db`
    and `sync/`'s node-identity files (absent from the archive means they
    didn't exist at backup time either, so the live copies are removed to
    match rather than left stale). `agentos.db` — not part of the archive,
    see module docstring — is wiped unconditionally rather than restored;
    callers are expected to re-initialize AgentOS and call
    `agentos.sync_agents()` afterward (api/backups.py's restore handler
    does this) so the registry gets rebuilt from the just-restored
    `rivulets.db` instead of carrying stale session state that disagrees
    with it. This function itself only touches files and the DB engine —
    no AgentOS/session dependency — so it stays usable standalone (as
    test_backup.py does) without those side effects.

    Disposes the current DB engine and clears the process-wide override so
    the next `get_engine()` call opens a fresh connection against the
    restored file — the doc's "stop, copy, delete WAL/SHM, start" restore
    procedure, without requiring an actual process restart. A real restart
    would also close out any in-flight request still holding a pre-restore
    connection, which this can't do from inside the app process itself —
    there's no process supervisor here to ask for one.

    Restoring `sync/`'s node-identity file onto a *running* node doesn't
    retroactively change an already-started SyncEngine's in-memory peer
    ID (sync/identity.py's key is read once, at engine start) — it only
    takes effect on the next process start, another case where a real
    restart would close the gap this function can't.
    """
    try:
        await create_backup(settings, get_engine(), prefix=PRE_RESTORE_PREFIX)
    except BackupIntegrityError:
        logger.exception("Pre-restore safety snapshot failed its integrity check")

    await get_engine().dispose()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(backup_path) as archive:
            archive.extractall(tmp_path, filter="data")

        _unlink_with_sidecars(settings.db_path)
        shutil.copyfile(tmp_path / _ARCHIVE_DB_NAME, settings.db_path)

        creds_src = tmp_path / _ARCHIVE_CREDENTIALS_NAME
        _unlink_with_sidecars(settings.credential_fallback_db_path)
        if creds_src.is_file():
            shutil.copyfile(creds_src, settings.credential_fallback_db_path)

        sync_src = tmp_path / _ARCHIVE_SYNC_DIRNAME
        if sync_src.is_dir():
            settings.sync_dir.mkdir(parents=True, exist_ok=True)
            for item in sync_src.iterdir():
                if item.is_file():
                    shutil.copyfile(item, settings.sync_dir / item.name)

    _unlink_with_sidecars(settings.agentos_db_path)
    override_engine(None)
