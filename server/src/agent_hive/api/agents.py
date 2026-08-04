"""Agent CRUD (FR-3) and routing rules (FR-3.3, FR-4.2).

Registering with AgentOS (FR-3.2) happens via agentos/service.py's
sync_agents() after every create/update/delete commit — see that module's
docstring for how "registration" works without an HTTP AgentOS API.
Routing rules are auto-generated via dispatch/rule_generation.py's LLM
call (FR-3.3, US-017) on create, and regenerated on update when the
description or instructions change (FR-3.4).

Agents are also the first entity type wired into P2P sync (FR-9.1's thin
first slice — see sync/apply.py's module docstring): every create/update
bumps this node's vector-clock component and publishes the new state to
peers. Publishing is best-effort — a peer being unreachable, or the sync
engine not running at all, must never fail the request (FR-9.5).
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from agent_hive.agentos import get_agentos, sync_agents
from agent_hive.api.deps import CurrentWorkspaceId, DbSession
from agent_hive.db.models import Agent, AgentRoutingRule, AgentTool, TeamAgent
from agent_hive.dispatch.rule_generation import generate_routing_rules
from agent_hive.sync.publish import publish_entity_change

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=10, max_length=500)
    instructions: str
    model: str  # "provider:model_name"
    tool_ids: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    model: str | None = None
    tool_ids: list[str] | None = None
    team_ids: list[str] | None = None


class AgentOut(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    model: str
    agentos_agent_id: str | None

    model_config = {"from_attributes": True}


class RoutingRuleOut(BaseModel):
    id: str
    rule_type: str
    pattern: str
    priority: int

    model_config = {"from_attributes": True}


class RoutingRuleIn(BaseModel):
    rule_type: str
    pattern: str
    priority: int = 0


class RoutingRulesUpdate(BaseModel):
    rules: list[RoutingRuleIn]


async def _get_or_404(db: DbSession, agent_id: str) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


async def _set_tools(db: DbSession, agent_id: str, tool_ids: list[str]) -> None:
    await db.execute(delete(AgentTool).where(AgentTool.agent_id == agent_id))
    for tool_id in tool_ids:
        db.add(AgentTool(agent_id=agent_id, tool_id=tool_id))


async def _set_teams(db: DbSession, agent_id: str, team_ids: list[str]) -> None:
    await db.execute(delete(TeamAgent).where(TeamAgent.agent_id == agent_id))
    for team_id in team_ids:
        db.add(TeamAgent(team_id=team_id, agent_id=agent_id))


async def _generate_and_store_routing_rules(db: DbSession, agent: Agent) -> None:
    rules = await generate_routing_rules(db, agent.name, agent.description, agent.instructions)
    if rules:
        db.add_all(
            AgentRoutingRule(
                agent_id=agent.id, rule_type=rule_type, pattern=pattern, priority=priority
            )
            for rule_type, pattern, priority in rules
        )
        await db.commit()


async def _publish_agent_change(db: DbSession, agent: Agent) -> None:
    await publish_entity_change(
        db,
        "agent",
        agent.id,
        {
            "name": agent.name,
            "description": agent.description,
            "instructions": agent.instructions,
            "model": agent.model,
        },
    )


async def _register_with_agentos(db: DbSession, agent: Agent) -> None:
    """Rebuild AgentOS's agent registry and record whether `agent` made it
    in. It won't have if its provider can't be resolved (NFR-2.4: that
    only takes the one agent offline, not the whole sync) — agentos_agent_id
    staying null is this scaffold's stand-in for an "unavailable" signal
    until the UI grows a real status indicator."""
    await sync_agents(db)
    registered = any(a.id == agent.id for a in (get_agentos().agents or []))
    agent.agentos_agent_id = agent.id if registered else None
    await db.commit()
    await db.refresh(agent)


@router.get("", response_model=list[AgentOut])
async def list_agents(db: DbSession, _: CurrentWorkspaceId) -> list[Agent]:
    result = await db.execute(select(Agent))
    return list(result.scalars().all())


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, db: DbSession, _: CurrentWorkspaceId) -> Agent:
    agent = Agent(
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        model=body.model,
    )
    db.add(agent)
    await db.flush()  # populate agent.id before using it in join rows

    await _set_tools(db, agent.id, body.tool_ids)
    await _set_teams(db, agent.id, body.team_ids)
    await db.commit()

    await _generate_and_store_routing_rules(db, agent)
    await _register_with_agentos(db, agent)
    await _publish_agent_change(db, agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: DbSession, _: CurrentWorkspaceId) -> Agent:
    return await _get_or_404(db, agent_id)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, body: AgentUpdate, db: DbSession, _: CurrentWorkspaceId
) -> Agent:
    agent = await _get_or_404(db, agent_id)
    rule_regen_fields = {"description", "instructions"}
    updates = body.model_dump(exclude_unset=True, exclude={"tool_ids", "team_ids"})
    needs_rule_regen = rule_regen_fields & updates.keys()

    for field, value in updates.items():
        setattr(agent, field, value)
    if body.tool_ids is not None:
        await _set_tools(db, agent_id, body.tool_ids)
    if body.team_ids is not None:
        await _set_teams(db, agent_id, body.team_ids)

    agent.vector_clock += 1
    await db.commit()

    if needs_rule_regen:
        await db.execute(delete(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent_id))
        await _generate_and_store_routing_rules(db, agent)

    await _register_with_agentos(db, agent)
    await _publish_agent_change(db, agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    agent = await _get_or_404(db, agent_id)
    await db.delete(agent)
    await db.commit()
    # Rebuilds AgentOS's registry from the remaining rows — the deleted
    # agent simply won't be in it anymore (FR-3.2's "unregister").
    await sync_agents(db)


@router.get("/{agent_id}/runs")
async def get_agent_runs(agent_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    """Run history (FR-3.5) — AgentOS agents are wired up and runnable now
    (see agentos/service.py), but reading run history back out of its
    SqliteDb (tokens, cost, status per run) hasn't been built yet."""
    await _get_or_404(db, agent_id)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Run history retrieval not yet wired up")


@router.get("/{agent_id}/routing-rules", response_model=list[RoutingRuleOut])
async def get_routing_rules(
    agent_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[AgentRoutingRule]:
    await _get_or_404(db, agent_id)
    result = await db.execute(
        select(AgentRoutingRule)
        .where(AgentRoutingRule.agent_id == agent_id)
        .order_by(AgentRoutingRule.priority.desc())
    )
    return list(result.scalars().all())


@router.patch("/{agent_id}/routing-rules", response_model=list[RoutingRuleOut])
async def update_routing_rules(
    agent_id: str, body: RoutingRulesUpdate, db: DbSession, _: CurrentWorkspaceId
) -> list[AgentRoutingRule]:
    await _get_or_404(db, agent_id)
    await db.execute(delete(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent_id))
    rows = [
        AgentRoutingRule(
            agent_id=agent_id,
            rule_type=r.rule_type,
            pattern=r.pattern,
            priority=r.priority,
        )
        for r in body.rules
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows
