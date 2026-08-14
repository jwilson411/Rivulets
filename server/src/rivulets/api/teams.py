"""Team CRUD (FR-2.2).

Also synced (FR-9.1) — name/description on `team` itself, and (#317)
membership as its own `team_agent` entity per row (sync/apply.py's
TEAM_AGENT_SPEC)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from rivulets.api.agents import agent_holds_owner_scope
from rivulets.api.deps import CurrentWorkspaceId, DbSession, SessionClaims, get_session_claims
from rivulets.db.models import Channel, Team, TeamAgent
from rivulets.sync.apply import TEAM_AGENT_SPEC
from rivulets.sync.publish import (
    publish_current_state,
    publish_tombstone,
    replace_join_entities,
)

router = APIRouter(prefix="/teams", tags=["teams"])


async def _publish_team_change(db: DbSession, team: Team) -> None:
    await publish_current_state(db, "team", team.id)


class TeamCreate(BaseModel):
    name: str
    description: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    agent_ids: list[str] | None = None


class TeamOut(BaseModel):
    id: str
    name: str
    description: str | None

    model_config = {"from_attributes": True}


class TeamDetailOut(TeamOut):
    agent_ids: list[str]


async def _get_or_404(db: DbSession, team_id: str) -> Team:
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    return team


async def _agent_ids(db: DbSession, team_id: str) -> list[str]:
    result = await db.execute(
        select(TeamAgent.agent_id).where(TeamAgent.team_id == team_id).order_by(TeamAgent.position)
    )
    return list(result.scalars().all())


async def team_holds_owner_scoped_agent(db: DbSession, team_id: str) -> bool:
    """#326: True if any agent currently on `team_id` holds an owner-granted
    capability scope (agents.py's agent_holds_owner_scope). A channel
    pointed at such a team can @mention that agent from chat, so retargeting
    Channel.team_id here is as much a confused-deputy escalation as editing
    the agent directly -- see channels.py's update_channel."""
    for agent_id in await _agent_ids(db, team_id):
        if await agent_holds_owner_scope(db, agent_id):
            return True
    return False


@router.get("", response_model=list[TeamOut])
async def list_teams(db: DbSession, _: CurrentWorkspaceId) -> list[Team]:
    result = await db.execute(select(Team))
    return list(result.scalars().all())


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(body: TeamCreate, db: DbSession, _: CurrentWorkspaceId) -> Team:
    team = Team(name=body.name, description=body.description)
    db.add(team)
    await db.commit()
    await db.refresh(team)
    await _publish_team_change(db, team)
    return team


@router.get("/{team_id}", response_model=TeamDetailOut)
async def get_team(team_id: str, db: DbSession, _: CurrentWorkspaceId) -> TeamDetailOut:
    team = await _get_or_404(db, team_id)
    return TeamDetailOut(
        id=team.id,
        name=team.name,
        description=team.description,
        agent_ids=await _agent_ids(db, team_id),
    )


@router.patch("/{team_id}", response_model=TeamDetailOut)
async def update_team(
    team_id: str,
    body: TeamUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> TeamDetailOut:
    team = await _get_or_404(db, team_id)
    if body.name is not None:
        team.name = body.name
    if body.description is not None:
        team.description = body.description
    old_agent_ids: set[str] | None = None
    new_agent_ids: set[str] | None = None
    if body.agent_ids is not None:
        old_agent_ids = set(await _agent_ids(db, team_id))
        new_agent_ids = set(dict.fromkeys(body.agent_ids))
        if claims.grant != "owner":
            # #326: chatting with an agent the owner already placed on a
            # shared channel is the documented un-gated surface -- but
            # adding a scoped agent to a team a guest controls, then
            # pointing (or already having pointed) a channel at it, is the
            # invocation-path half of #231's confused-deputy hole. Only
            # newly-added agents are checked: one already on the team got
            # there via a write that was itself gated at the time.
            for agent_id in new_agent_ids - old_agent_ids:
                if await agent_holds_owner_scope(db, agent_id):
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        "Owner access required to add an agent that holds a capability "
                        "scope to a team",
                    )
        await db.execute(delete(TeamAgent).where(TeamAgent.team_id == team_id))
        for position, agent_id in enumerate(body.agent_ids):
            db.add(TeamAgent(team_id=team_id, agent_id=agent_id, position=position))
    await db.commit()
    await _publish_team_change(db, team)
    if old_agent_ids is not None and new_agent_ids is not None:
        await replace_join_entities(
            db,
            "team_agent",
            TEAM_AGENT_SPEC,
            {(team_id, agent_id) for agent_id in old_agent_ids},
            {(team_id, agent_id) for agent_id in new_agent_ids},
        )
    return TeamDetailOut(
        id=team.id,
        name=team.name,
        description=team.description,
        agent_ids=await _agent_ids(db, team_id),
    )


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(team_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    team = await _get_or_404(db, team_id)
    # Channel.team_id has no ondelete (SQLite defaults an unset FK to
    # RESTRICT), so a channel still pointing at this team would otherwise
    # turn the delete below into an unhandled IntegrityError (#250). Mirror
    # the "clear children first" pattern api/mcp_servers.py's delete_server
    # uses for its Tool rows: unassign rather than block, since a deleted
    # team is a smaller surprise to a channel than a delete that silently
    # fails.
    await db.execute(update(Channel).where(Channel.team_id == team_id).values(team_id=None))
    await db.delete(team)
    await db.commit()
    # #238: without this, a peer that still has the row would keep it, and
    # its own next edit would arrive here as a plain REMOTE_NEWER update
    # and recreate the team we just deleted.
    await publish_tombstone(db, "team", team_id)
