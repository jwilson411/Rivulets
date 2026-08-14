"""Channel CRUD (FR-2.1, FR-2.4, FR-2.5).

Also synced (FR-9.1) — name/description/position/archived/team_id (#317:
see sync/apply.py's CHANNEL_SPEC)."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import Channel
from rivulets.sync.publish import publish_current_state

router = APIRouter(prefix="/channels", tags=["channels"])


async def _publish_channel_change(db: DbSession, channel: Channel) -> None:
    await publish_current_state(db, "channel", channel.id)


class ChannelCreate(BaseModel):
    name: str
    description: str | None = None


class ChannelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    team_id: str | None = None


class ChannelOut(BaseModel):
    id: str
    name: str
    description: str | None
    team_id: str | None
    position: int
    archived: bool

    model_config = {"from_attributes": True}


class ReorderRequest(BaseModel):
    order: list[str]


async def _get_or_404(db: DbSession, channel_id: str) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    return channel


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: DbSession, _: CurrentWorkspaceId) -> list[Channel]:
    result = await db.execute(select(Channel).order_by(Channel.position))
    return list(result.scalars().all())


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, db: DbSession, _: CurrentWorkspaceId) -> Channel:
    if not (3 <= len(body.name) <= 80):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be 3-80 chars")
    channel = Channel(name=body.name, description=body.description)
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    await _publish_channel_change(db, channel)
    return channel


@router.patch("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_channels(body: ReorderRequest, db: DbSession, _: CurrentWorkspaceId) -> None:
    channels = [await _get_or_404(db, channel_id) for channel_id in body.order]
    for position, channel in enumerate(channels):
        channel.position = position
    await db.commit()
    for channel in channels:
        await _publish_channel_change(db, channel)


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> Channel:
    return await _get_or_404(db, channel_id)


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str, body: ChannelUpdate, db: DbSession, _: CurrentWorkspaceId
) -> Channel:
    channel = await _get_or_404(db, channel_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(channel, field, value)
    channel.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    channel.vector_clock += 1
    await db.commit()
    await db.refresh(channel)
    await _publish_channel_change(db, channel)
    return channel


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_channel(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    """Soft delete (FR-2.5) — archived channels are recoverable, not destroyed."""
    channel = await _get_or_404(db, channel_id)
    channel.archived = True
    await db.commit()
    await _publish_channel_change(db, channel)


@router.post("/{channel_id}/unarchive", response_model=ChannelOut)
async def unarchive_channel(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> Channel:
    channel = await _get_or_404(db, channel_id)
    channel.archived = False
    await db.commit()
    await db.refresh(channel)
    await _publish_channel_change(db, channel)
    return channel
