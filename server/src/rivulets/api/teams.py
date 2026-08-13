"""Team CRUD (FR-2.2).

Also synced (FR-9.1) — name/description only; team membership (agent_ids,
a join table) isn't synced yet for the same foreign-key-ordering reason
Channel.team_id isn't (see sync/apply.py's module docstring)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import Channel, Team, TeamAgent
from rivulets.sync.publish import publish_current_state, publish_tombstone

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
    team_id: str, body: TeamUpdate, db: DbSession, _: CurrentWorkspaceId
) -> TeamDetailOut:
    team = await _get_or_404(db, team_id)
    if body.name is not None:
        team.name = body.name
    if body.description is not None:
        team.description = body.description
    if body.agent_ids is not None:
        await db.execute(delete(TeamAgent).where(TeamAgent.team_id == team_id))
        for position, agent_id in enumerate(body.agent_ids):
            db.add(TeamAgent(team_id=team_id, agent_id=agent_id, position=position))
    await db.commit()
    await _publish_team_change(db, team)
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
