"""Shared agent-mutation side effects: tool/team assignment, #104 version
snapshots, FR-3.3 routing-rule generation, FR-3.2 AgentOS registration, and
FR-9.1 sync publish.

Originally private helpers inside api/agents.py (`_set_tools`, `_set_teams`,
`_generate_and_store_routing_rules`, `_publish_agent_change`,
`_record_agent_version`, `_register_with_agentos`) -- pulled out here (#190)
so dispatch/service.py's create_agent/update_agent/rollback_agent_version
built-in tool handlers can give an agent-created agent the exact same "not
really usable until it's versioned, has routing rules, and is registered
with AgentOS" treatment a human's own API call gets, rather than either
duplicating this logic a second time (likely to drift) or having
dispatch/service.py -- a lower layer that api/ already depends on --
reach up and import from api/agents.py.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.service import get_agentos, sync_agents
from rivulets.db.models import Agent, AgentRoutingRule, AgentTool, AgentVersion, TeamAgent
from rivulets.sync.apply import AGENT_TOOL_SPEC, TEAM_AGENT_SPEC
from rivulets.sync.publish import (
    publish_current_state,
    publish_tombstone,
    replace_join_entities,
)


async def set_agent_tools(
    db: AsyncSession, agent_id: str, tool_ids: list[str]
) -> tuple[set[str], set[str]]:
    """De-dupes `tool_ids` (`dict.fromkeys` preserves first-seen order)
    before inserting -- AgentTool's primary key is the (agent_id, tool_id)
    pair, so a caller-supplied list with a repeat would otherwise hit an
    IntegrityError on commit. A hand-built UI request is unlikely to repeat
    an id, but a #190 built-in tool call assembled by a model is a much
    likelier source of one.

    Still doesn't commit -- every caller folds this into a larger
    transaction alongside other agent-field edits. Returns
    (old_tool_ids, new_tool_ids) so the caller can publish the diff
    (#317, publish_agent_tools_change below) once *its* commit has
    actually landed, rather than this function publishing rows that
    might still get rolled back by something the caller does next."""
    old_ids = set(
        (
            await db.scalars(select(AgentTool.tool_id).where(AgentTool.agent_id == agent_id))
        ).all()
    )
    await db.execute(delete(AgentTool).where(AgentTool.agent_id == agent_id))
    new_ids = set(dict.fromkeys(tool_ids))
    for tool_id in new_ids:
        db.add(AgentTool(agent_id=agent_id, tool_id=tool_id))
    return old_ids, new_ids


async def publish_agent_tools_change(
    db: AsyncSession, agent_id: str, old_tool_ids: set[str], new_tool_ids: set[str]
) -> None:
    await replace_join_entities(
        db,
        "agent_tool",
        AGENT_TOOL_SPEC,
        {(agent_id, tool_id) for tool_id in old_tool_ids},
        {(agent_id, tool_id) for tool_id in new_tool_ids},
    )


async def set_agent_teams(
    db: AsyncSession, agent_id: str, team_ids: list[str]
) -> tuple[set[str], set[str]]:
    """See set_agent_tools' docstring -- same de-dupe (TeamAgent's primary
    key is the (team_id, agent_id) pair), same no-commit/returns-the-diff
    contract."""
    old_ids = set(
        (
            await db.scalars(select(TeamAgent.team_id).where(TeamAgent.agent_id == agent_id))
        ).all()
    )
    await db.execute(delete(TeamAgent).where(TeamAgent.agent_id == agent_id))
    new_ids = set(dict.fromkeys(team_ids))
    for team_id in new_ids:
        db.add(TeamAgent(team_id=team_id, agent_id=agent_id))
    return old_ids, new_ids


async def publish_agent_teams_change(
    db: AsyncSession, agent_id: str, old_team_ids: set[str], new_team_ids: set[str]
) -> None:
    await replace_join_entities(
        db,
        "team_agent",
        TEAM_AGENT_SPEC,
        {(team_id, agent_id) for team_id in old_team_ids},
        {(team_id, agent_id) for team_id in new_team_ids},
    )


async def replace_routing_rules(
    db: AsyncSession, agent_id: str, rules: list[tuple[str, str, int]]
) -> None:
    """Delete-then-recreate replace of an agent's routing rules (FR-3.3) --
    the shared implementation behind both auto-generation
    (generate_and_store_routing_rules below) and every manual full-replace
    caller (api/agents.py's update_routing_rules, dispatch/service.py's
    _handle_update_agent_routing_rules_trigger). Commits and publishes the
    resulting diff itself (#317): unlike set_agent_tools/set_agent_teams
    above, every existing caller already treated a routing-rule replace as
    its own separate step (generate_and_store_routing_rules always
    committed on its own, even before #317), so there's no larger
    transaction here to preserve atomicity with. AgentRoutingRule has a
    real single-column `id` (unlike AgentTool/TeamAgent), so each row is
    published individually through the ordinary AGENT_ROUTING_RULE_SPEC
    path rather than replace_join_entities' composite-key diffing."""
    old_ids = set(
        (
            await db.scalars(
                select(AgentRoutingRule.id).where(AgentRoutingRule.agent_id == agent_id)
            )
        ).all()
    )
    await db.execute(delete(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent_id))
    new_rows = [
        AgentRoutingRule(agent_id=agent_id, rule_type=rule_type, pattern=pattern, priority=priority)
        for rule_type, pattern, priority in rules
    ]
    db.add_all(new_rows)
    await db.commit()
    for row in new_rows:
        await db.refresh(row)

    for old_id in old_ids:
        await publish_tombstone(db, "agent_routing_rule", old_id)
    for row in new_rows:
        await publish_current_state(db, "agent_routing_rule", row.id)


async def generate_and_store_routing_rules(db: AsyncSession, agent: Agent) -> None:
    """Imports dispatch/rule_generation.py lazily: that module lives in the
    `dispatch` package, whose own __init__.py imports dispatch/service.py
    (which imports this module) -- deferring to call time, well after
    both packages are fully loaded, sidesteps any import-order risk
    the same way _handle_run_workflow_trigger's lazy `rivulets.workflows`
    import does in dispatch/service.py."""
    from rivulets.dispatch.rule_generation import generate_routing_rules

    rules = await generate_routing_rules(db, agent.name, agent.description, agent.instructions)
    await replace_routing_rules(db, agent.id, rules)


async def publish_agent_change(db: AsyncSession, agent: Agent) -> None:
    await publish_current_state(db, "agent", agent.id)


async def record_agent_version(db: AsyncSession, agent: Agent) -> None:
    """Snapshots the agent's *current* instructions/model as the next
    version (#104) -- same "record what's now current" shape as tools.py's
    save_tool_version, just triggered from the CRUD/tool-call flow itself
    rather than a dedicated "save" endpoint, since agent edits don't have a
    separate editor-handoff step the way custom tool source code does."""
    latest_version = await db.scalar(
        select(AgentVersion.version)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.version.desc())
        .limit(1)
    )
    db.add(
        AgentVersion(
            agent_id=agent.id,
            version=(latest_version or 0) + 1,
            instructions=agent.instructions,
            model=agent.model,
        )
    )


async def register_agent_with_agentos(db: AsyncSession, agent: Agent) -> None:
    """Rebuild AgentOS's agent registry and record whether `agent` made it
    in. It won't have if its provider can't be resolved (NFR-2.4: that
    only takes the one agent offline, not the whole sync) -- agentos_agent_id
    staying null is this scaffold's stand-in for an "unavailable" signal
    until the UI grows a real status indicator."""
    await sync_agents(db)
    registered = any(a.id == agent.id for a in (get_agentos().agents or []))
    agent.agentos_agent_id = agent.id if registered else None
    await db.commit()
    await db.refresh(agent)
