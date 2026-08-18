"""Channel CRUD (FR-2.1, FR-2.4, FR-2.5).

Also synced (FR-9.1) — name/description/position/archived/team_id (#317:
see sync/apply.py's CHANNEL_SPEC)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from rivulets.api.deps import CurrentWorkspaceId, DbSession, SessionClaims, get_session_claims
from rivulets.api.teams import team_holds_owner_scoped_agent
from rivulets.db.models import Channel, Team
from rivulets.sync.publish import publish_current_state
from rivulets.working_directory import normalize_working_directory, resolve_effective_path

router = APIRouter(prefix="/channels", tags=["channels"])


async def _publish_channel_change(db: DbSession, channel: Channel) -> None:
    await publish_current_state(db, "channel", channel.id)


class ChannelCreate(BaseModel):
    name: str
    description: str | None = None
    # #411: assign a team at create so the first message can get a reply.
    team_id: str | None = None


class ChannelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    team_id: str | None = None
    working_directory: str | None = None


class ChannelOut(BaseModel):
    id: str
    name: str
    description: str | None
    team_id: str | None
    position: int
    archived: bool
    working_directory: str | None = None
    effective_working_directory: str | None = None

    model_config = {"from_attributes": True}


def _to_channel_out(channel: Channel) -> ChannelOut:
    return ChannelOut(
        id=channel.id,
        name=channel.name,
        description=channel.description,
        team_id=channel.team_id,
        position=channel.position,
        archived=channel.archived,
        working_directory=channel.working_directory,
        effective_working_directory=resolve_effective_path(channel.working_directory),
    )


class ReorderRequest(BaseModel):
    order: list[str]


async def _get_or_404(db: DbSession, channel_id: str) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    return channel


def _require_name_length(name: str) -> None:
    if not (3 <= len(name) <= 80):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be 3-80 chars")


def _name_conflict(name: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, f"A channel named {name!r} already exists")


async def _require_name_available(
    db: DbSession, name: str, *, except_id: str | None = None
) -> None:
    """Mirrors api/agents.py's _check_name_available against idx_channel_name
    (unique among non-archived channels) so the common case is a 409 with a
    plain sentence instead of app.py's generic IntegrityError safety net."""
    query = select(Channel).where(Channel.name == name, Channel.archived.is_(False))
    if except_id is not None:
        query = query.where(Channel.id != except_id)
    existing = await db.scalar(query)
    if existing is not None:
        raise _name_conflict(name)


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: DbSession, _: CurrentWorkspaceId) -> list[ChannelOut]:
    result = await db.execute(select(Channel).order_by(Channel.position))
    return [_to_channel_out(channel) for channel in result.scalars().all()]


async def _require_assignable_team(db: DbSession, claims: SessionClaims, team_id: str) -> None:
    if await db.get(Team, team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    if claims.grant != "owner" and await team_holds_owner_scoped_agent(db, team_id):
        # #326: same confused-deputy concern as update_channel — pointing
        # a channel at a team that already has a capability-scoped agent
        # makes that agent @mention-able from chat.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Owner access required to point a channel at a team with a capability-scoped agent",
        )


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelCreate,
    db: DbSession,
    _: CurrentWorkspaceId,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> ChannelOut:
    _require_name_length(body.name)
    await _require_name_available(db, body.name)
    if body.team_id is not None:
        await _require_assignable_team(db, claims, body.team_id)
    channel = Channel(name=body.name, description=body.description, team_id=body.team_id)
    db.add(channel)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Pre-check closes the common case; this closes the race between
        # it and commit (two concurrent creates of the same active name).
        await db.rollback()
        raise _name_conflict(body.name) from exc
    await db.refresh(channel)
    await _publish_channel_change(db, channel)
    return _to_channel_out(channel)


@router.patch("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_channels(body: ReorderRequest, db: DbSession, _: CurrentWorkspaceId) -> None:
    channels = [await _get_or_404(db, channel_id) for channel_id in body.order]
    for position, channel in enumerate(channels):
        channel.position = position
    await db.commit()
    for channel in channels:
        await _publish_channel_change(db, channel)


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> ChannelOut:
    return _to_channel_out(await _get_or_404(db, channel_id))


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str,
    body: ChannelUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> ChannelOut:
    channel = await _get_or_404(db, channel_id)
    if (
        claims.grant != "owner"
        and "team_id" in body.model_fields_set
        and body.team_id is not None
        and body.team_id != channel.team_id
        and await team_holds_owner_scoped_agent(db, body.team_id)
    ):
        # #326: same confused-deputy concern as update_team's new-agent
        # gate -- pointing a channel at a team that already has a
        # capability-scoped agent on it makes that agent @mention-able
        # from chat, same as adding it to the team directly.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Owner access required to point a channel at a team with a capability-scoped agent",
        )
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        _require_name_length(updates["name"])
        if updates["name"] != channel.name:
            await _require_name_available(db, updates["name"], except_id=channel.id)
    if "working_directory" in updates:
        if claims.grant != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Owner access required to set a channel project folder",
            )
        try:
            updates["working_directory"] = normalize_working_directory(updates["working_directory"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    synced_changed = any(field != "working_directory" for field in updates)
    for field, value in updates.items():
        setattr(channel, field, value)
    channel.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # working_directory is node-local and excluded from CHANNEL_SPEC —
    # bumping the clock / publishing for a folder-only change would
    # gossip a no-op rivulet-unrelated channel revision to peers.
    if synced_changed:
        channel.vector_clock += 1
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _name_conflict(channel.name) from exc
    await db.refresh(channel)
    if synced_changed:
        await _publish_channel_change(db, channel)
    return _to_channel_out(channel)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_channel(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    """Soft delete (FR-2.5) — archived channels are recoverable, not destroyed."""
    channel = await _get_or_404(db, channel_id)
    channel.archived = True
    await db.commit()
    await _publish_channel_change(db, channel)


@router.post("/{channel_id}/unarchive", response_model=ChannelOut)
async def unarchive_channel(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> ChannelOut:
    channel = await _get_or_404(db, channel_id)
    await _require_name_available(db, channel.name, except_id=channel.id)
    channel.archived = False
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _name_conflict(channel.name) from exc
    await db.refresh(channel)
    await _publish_channel_change(db, channel)
    return _to_channel_out(channel)
