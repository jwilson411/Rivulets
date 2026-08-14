"""Backup & restore endpoints (security-and-dr.md's "Manual Backup" trigger
and restore procedure). Automatic daily/pre-upgrade snapshots run from
app.py's lifespan, not from here — this router only covers what a user
triggers directly: list snapshots, back up now, restore from one.

No DbSession dependency for listing/creating: those act on the SQLite
file(s) directly (via the engine, for a live VACUUM INTO snapshot), not
through the ORM. Restore is the exception — after backup.restore_from_backup
swaps the files in, this router (not that function — see its docstring)
re-initializes AgentOS and calls sync_agents() against a fresh session so
the in-process agent registry agrees with the just-restored `rivulets.db`
(#243).
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from rivulets.agentos import init_agentos, reset_agentos, sync_agents
from rivulets.api.deps import CurrentWorkspaceId, OwnerGrant
from rivulets.backup import (
    MANUAL_PREFIX,
    BackupInfo,
    BackupIntegrityError,
    RestoreIntegrityError,
    create_backup,
    list_backups,
    resolve_backup_path,
    restore_from_backup,
)
from rivulets.config import get_settings
from rivulets.db.session import get_engine, session_scope

router = APIRouter(prefix="/backups", tags=["backups"])


class BackupOut(BaseModel):
    filename: str
    kind: str
    size_bytes: int
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_info(cls, info: BackupInfo) -> "BackupOut":
        return cls(
            filename=info.filename,
            kind=info.kind,
            size_bytes=info.size_bytes,
            created_at=info.created_at,
        )


@router.get("", response_model=list[BackupOut])
async def get_backups(_: CurrentWorkspaceId, _o: OwnerGrant) -> list[BackupOut]:
    return [BackupOut.from_info(info) for info in list_backups(get_settings())]


@router.post("", response_model=BackupOut, status_code=status.HTTP_201_CREATED)
async def create_manual_backup(_: CurrentWorkspaceId, _o: OwnerGrant) -> BackupOut:
    settings = get_settings()
    try:
        path = await create_backup(settings, get_engine(), prefix=MANUAL_PREFIX)
    except BackupIntegrityError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    info = next(b for b in list_backups(settings) if b.filename == path.name)
    return BackupOut.from_info(info)


class RestoreIn(BaseModel):
    # A silent clobber with no confirmation was #243's own complaint —
    # this forces the caller (the UI's typed-confirmation panel) to echo
    # the exact filename back, not just any truthy flag, so a stray
    # scripted retry can't accidentally confirm a *different* restore.
    confirm_filename: str


@router.post("/{filename}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_backup(
    filename: str, body: RestoreIn, _: CurrentWorkspaceId, _o: OwnerGrant
) -> None:
    settings = get_settings()
    backup_path = resolve_backup_path(settings, filename)
    if backup_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup not found")
    if body.confirm_filename != filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmation does not match filename")
    try:
        await restore_from_backup(settings, backup_path)
    except RestoreIntegrityError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    # restore_from_backup wipes agentos.db but deliberately doesn't touch
    # the AgentOS singleton itself (its own docstring explains why) — do
    # that here so the registry is rebuilt from the just-restored DB
    # before this request returns.
    reset_agentos()
    init_agentos()
    async with session_scope() as db:
        await sync_agents(db)
