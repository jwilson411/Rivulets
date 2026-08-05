"""File upload/download (FR-10.1, FR-10.2).

Content-addressed storage at `~/.rivulets/files/{hash[0:2]}/{full_hash}`
per docs/infrastructure/compute-and-storage.md — identical uploads share
one copy on disk.

Also synced (FR-9.1/FR-9.7) — publish_file_change is exported for
api/rivulets.py to reuse too: a file's message_id only gets set once it's
attached to a message (uploads are a separate, prior step), so the
message-attach path needs to republish the same File row again with its
now-current message_id, not just the upload path."""

import hashlib

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.config import get_settings
from rivulets.db.models import File as FileRow
from rivulets.sync.publish import publish_current_state

router = APIRouter(prefix="/files", tags=["files"])

_MAX_FILE_BYTES = 100 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


async def publish_file_change(db: DbSession, file_row: FileRow) -> None:
    await publish_current_state(db, "file", file_row.id)


class FileOut(BaseModel):
    file_id: str
    content_hash: str
    filename: str
    mime_type: str
    size_bytes: int

    model_config = {"from_attributes": True, "populate_by_name": True}


async def _get_or_404(db: DbSession, file_id: str) -> FileRow:
    row = await db.get(FileRow, file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row


@router.post("/upload", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(upload: UploadFile, db: DbSession, _: CurrentWorkspaceId) -> FileOut:
    settings = get_settings()
    hasher = hashlib.sha256()
    size = 0
    chunks: list[bytes] = []
    while chunk := await upload.read(_CHUNK_SIZE):
        size += len(chunk)
        if size > _MAX_FILE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 100MB limit"
            )
        hasher.update(chunk)
        chunks.append(chunk)

    content_hash = hasher.hexdigest()
    dest_dir = settings.files_dir / content_hash[:2]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / content_hash
    if not dest_path.exists():  # dedupe identical content
        with dest_path.open("wb") as f:
            f.writelines(chunks)

    row = FileRow(
        content_hash=content_hash,
        filename=upload.filename or content_hash,
        mime_type=upload.content_type or "application/octet-stream",
        size_bytes=size,
        local_path=str(dest_path),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await publish_file_change(db, row)
    return FileOut(
        file_id=row.id,
        content_hash=row.content_hash,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
    )


@router.get("/{file_id}")
async def download_file(file_id: str, db: DbSession, _: CurrentWorkspaceId) -> FileResponse:
    row = await _get_or_404(db, file_id)
    return FileResponse(row.local_path, media_type=row.mime_type, filename=row.filename)


@router.get("/{file_id}/info", response_model=FileOut)
async def file_info(file_id: str, db: DbSession, _: CurrentWorkspaceId) -> FileOut:
    row = await _get_or_404(db, file_id)
    return FileOut(
        file_id=row.id,
        content_hash=row.content_hash,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
    )
