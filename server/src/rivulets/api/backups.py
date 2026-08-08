"""Backup & restore endpoints (security-and-dr.md's "Manual Backup" trigger
and restore procedure). Automatic daily/pre-upgrade snapshots run from
app.py's lifespan, not from here — this router only covers what a user
triggers directly: list snapshots, back up now, restore from one.

No DbSession dependency: backups act on the SQLite file directly (via the
engine, for a live VACUUM INTO snapshot), not through the ORM.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from rivulets.api.deps import CurrentWorkspaceId, OwnerGrant
from rivulets.backup import (
    MANUAL_PREFIX,
    BackupInfo,
    BackupIntegrityError,
    create_backup,
    list_backups,
    resolve_backup_path,
    restore_from_backup,
)
from rivulets.config import get_settings
from rivulets.db.session import get_engine

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


@router.post("/{filename}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_backup(filename: str, _: CurrentWorkspaceId, _o: OwnerGrant) -> None:
    settings = get_settings()
    backup_path = resolve_backup_path(settings, filename)
    if backup_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup not found")
    await restore_from_backup(settings, backup_path)
