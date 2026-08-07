"""Starter agent/team library seeding (#16)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import AUTO_MODEL
from rivulets.agentos.starter_content import (
    _STARTER_AGENTS,
    _STARTER_TEAM_NAME,
    seed_starter_agents,
    seed_starter_teams,
)
from rivulets.agentos.tool_resolution import seed_builtin_tools
from rivulets.db.models import Agent, AgentTool, Team, TeamAgent, Tool

_STARTER_AGENT_NAMES = {starter.name for starter in _STARTER_AGENTS}


async def test_seed_starter_agents_creates_rows(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    result = await db_session.execute(select(Agent.name))
    assert set(result.scalars().all()) == _STARTER_AGENT_NAMES


async def test_seed_starter_agents_use_auto_model(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    result = await db_session.execute(select(Agent))
    assert all(agent.model == AUTO_MODEL for agent in result.scalars().all())


async def test_seed_starter_agents_is_idempotent(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)
    await seed_starter_agents(db_session)

    result = await db_session.execute(select(Agent.name))
    names = list(result.scalars().all())
    assert len(names) == len(set(names))


async def test_seed_starter_agents_assigns_expected_tools(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    coder = (await db_session.execute(select(Agent).where(Agent.name == "Coder"))).scalar_one()
    result = await db_session.execute(
        select(Tool.name)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == coder.id)
    )
    assert set(result.scalars().all()) == {
        "read_file",
        "write_file",
        "list_files",
        "execute_python",
    }

    assistant = (
        await db_session.execute(select(Agent).where(Agent.name == "Assistant"))
    ).scalar_one()
    result = await db_session.execute(
        select(AgentTool).where(AgentTool.agent_id == assistant.id)
    )
    assert result.scalars().all() == []


async def test_seed_starter_agents_without_builtin_tools_still_creates_agents(
    db_session: AsyncSession,
) -> None:
    """If seed_builtin_tools() somehow hasn't run yet, starter agents are
    still created -- just without tool assignments -- rather than this
    failing outright."""
    await seed_starter_agents(db_session)

    result = await db_session.execute(select(Agent.name))
    assert set(result.scalars().all()) == _STARTER_AGENT_NAMES

    result = await db_session.execute(select(AgentTool))
    assert result.scalars().all() == []


async def test_seed_starter_teams_creates_team_with_all_agents(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)
    await seed_starter_teams(db_session)

    team = (
        await db_session.execute(select(Team).where(Team.name == _STARTER_TEAM_NAME))
    ).scalar_one()
    result = await db_session.execute(
        select(TeamAgent).where(TeamAgent.team_id == team.id).order_by(TeamAgent.position)
    )
    assert len(result.scalars().all()) == len(_STARTER_AGENTS)


async def test_seed_starter_teams_is_idempotent(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)
    await seed_starter_teams(db_session)
    await seed_starter_teams(db_session)

    result = await db_session.execute(select(Team).where(Team.name == _STARTER_TEAM_NAME))
    assert len(result.scalars().all()) == 1


async def test_seed_starter_teams_without_agents_creates_empty_team(
    db_session: AsyncSession,
) -> None:
    """No starter agents exist yet -- the team is still created, just
    with no members, rather than failing."""
    await seed_starter_teams(db_session)

    team = (
        await db_session.execute(select(Team).where(Team.name == _STARTER_TEAM_NAME))
    ).scalar_one()
    result = await db_session.execute(select(TeamAgent).where(TeamAgent.team_id == team.id))
    assert result.scalars().all() == []
