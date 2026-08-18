"""Starter agent/team library seeding (#16)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import AUTO_MODEL
from rivulets.agentos.starter_content import (
    _ASSISTANT_ORCHESTRATOR_INSTRUCTIONS,  # pyright: ignore[reportPrivateUsage]
    _LEGACY_ASSISTANT_INSTRUCTIONS,  # pyright: ignore[reportPrivateUsage]
    _PREVIOUS_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS,  # pyright: ignore[reportPrivateUsage]
    _STARTER_AGENTS,  # pyright: ignore[reportPrivateUsage]
    _STARTER_TEAM_NAME,  # pyright: ignore[reportPrivateUsage]
    _STARTER_TOOL_NAMES,  # pyright: ignore[reportPrivateUsage]
    _UNRESTRICTED_STARTER_MARKERS,  # pyright: ignore[reportPrivateUsage]
    ensure_assistant_always_rule,
    ensure_assistant_orchestrator_instructions,
    ensure_starter_agents_chat_safe_tools,
    repair_generated_routing_rules,
    seed_starter_agents,
    seed_starter_teams,
)
from rivulets.agentos.tool_resolution import resolve_agent_tools, seed_builtin_tools
from rivulets.agentos.tool_scopes import DEFAULT_AGENT_SCOPES
from rivulets.db.models import (
    Agent,
    AgentRoutingRule,
    AgentTool,
    AgentToolScope,
    Team,
    TeamAgent,
    Tool,
)
from rivulets.dispatch.rule_generation import starter_keyword_rule

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


async def test_seed_starter_agents_assigns_chat_safe_tools(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    for starter_name in _STARTER_AGENT_NAMES:
        agent = (
            await db_session.execute(select(Agent).where(Agent.name == starter_name))
        ).scalar_one()
        assigned = set(
            (
                await db_session.execute(
                    select(Tool.name)
                    .join(AgentTool, AgentTool.tool_id == Tool.id)
                    .where(AgentTool.agent_id == agent.id)
                )
            ).scalars()
        )
        assert assigned == set(_STARTER_TOOL_NAMES[starter_name])
        assert assigned.isdisjoint(_UNRESTRICTED_STARTER_MARKERS)


async def test_seed_starter_agents_grants_no_capability_scopes(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    for starter_name in _STARTER_AGENT_NAMES:
        agent = (
            await db_session.execute(select(Agent).where(Agent.name == starter_name))
        ).scalar_one()
        scopes = set(
            (
                await db_session.execute(
                    select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
                )
            ).scalars()
        )
        assert scopes == set()


async def test_seed_starter_agents_sensitive_tools_do_not_resolve(
    db_session: AsyncSession,
) -> None:
    """#462: assignment + scope are both withheld, so resolve_agent_tools
    cannot return admin or sensitive builtins for a starter."""
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    for starter_name in _STARTER_AGENT_NAMES:
        agent = (
            await db_session.execute(select(Agent).where(Agent.name == starter_name))
        ).scalar_one()
        resolved = {fn.name for fn in await resolve_agent_tools(db_session, agent)}
        assert resolved == set(_STARTER_TOOL_NAMES[starter_name])
        assert resolved.isdisjoint(_UNRESTRICTED_STARTER_MARKERS)


async def test_ensure_starter_agents_chat_safe_tools_retracts_unrestricted_seed(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = Agent(
        name="Assistant",
        description="the #459 every-tool seed",
        instructions="You are helpful.",
        model=AUTO_MODEL,
    )
    db_session.add(agent)
    await db_session.flush()
    builtins = list(
        (await db_session.execute(select(Tool).where(Tool.tool_type == "builtin"))).scalars()
    )
    for tool_row in builtins:
        db_session.add(AgentTool(agent_id=agent.id, tool_id=tool_row.id))
    for scope in DEFAULT_AGENT_SCOPES:
        db_session.add(AgentToolScope(agent_id=agent.id, scope=scope))
    await db_session.commit()

    await ensure_starter_agents_chat_safe_tools(db_session)

    assigned = set(
        (
            await db_session.execute(
                select(Tool.name)
                .join(AgentTool, AgentTool.tool_id == Tool.id)
                .where(AgentTool.agent_id == agent.id)
            )
        ).scalars()
    )
    scopes = set(
        (
            await db_session.execute(
                select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
            )
        ).scalars()
    )
    assert assigned == set(_STARTER_TOOL_NAMES["Assistant"])
    assert scopes == set()


async def test_ensure_starter_agents_chat_safe_tools_leaves_legacy_curated_set(
    db_session: AsyncSession,
) -> None:
    """Pre-#459 Coder (write + exec, no scopes) is not upgraded and not
    stripped -- the owner never got the all-permissions grant."""
    await seed_builtin_tools(db_session)
    agent = Agent(
        name="Coder",
        description="legacy starter with a curated tool set",
        instructions="You write code.",
        model=AUTO_MODEL,
    )
    db_session.add(agent)
    await db_session.flush()
    for name in ("read_file", "write_file", "list_files", "execute_python"):
        tool_row = (await db_session.execute(select(Tool).where(Tool.name == name))).scalar_one()
        db_session.add(AgentTool(agent_id=agent.id, tool_id=tool_row.id))
    await db_session.commit()

    await ensure_starter_agents_chat_safe_tools(db_session)

    assigned = set(
        (
            await db_session.execute(
                select(Tool.name)
                .join(AgentTool, AgentTool.tool_id == Tool.id)
                .where(AgentTool.agent_id == agent.id)
            )
        ).scalars()
    )
    scopes = set(
        (
            await db_session.execute(
                select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
            )
        ).scalars()
    )
    assert assigned == {"read_file", "write_file", "list_files", "execute_python"}
    assert scopes == set()


async def test_ensure_starter_agents_chat_safe_tools_leaves_explicit_owner_grant(
    db_session: AsyncSession,
) -> None:
    """Owner granted execute_python + its scope, not the all-scopes seed."""
    await seed_builtin_tools(db_session)
    agent = Agent(
        name="Coder",
        description="owner enabled code exec",
        instructions="You write code.",
        model=AUTO_MODEL,
    )
    db_session.add(agent)
    await db_session.flush()
    for name in ("read_file", "list_files", "execute_python"):
        tool_row = (await db_session.execute(select(Tool).where(Tool.name == name))).scalar_one()
        db_session.add(AgentTool(agent_id=agent.id, tool_id=tool_row.id))
    db_session.add(AgentToolScope(agent_id=agent.id, scope="sensitive_tools:manage"))
    await db_session.commit()

    await ensure_starter_agents_chat_safe_tools(db_session)

    assigned = set(
        (
            await db_session.execute(
                select(Tool.name)
                .join(AgentTool, AgentTool.tool_id == Tool.id)
                .where(AgentTool.agent_id == agent.id)
            )
        ).scalars()
    )
    scopes = set(
        (
            await db_session.execute(
                select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
            )
        ).scalars()
    )
    assert assigned == {"read_file", "list_files", "execute_python"}
    assert scopes == {"sensitive_tools:manage"}


async def test_ensure_starter_agents_chat_safe_tools_leaves_customized_starter(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = Agent(
        name="Assistant",
        description="owner already trimmed this starter's tools",
        instructions="You are helpful.",
        model=AUTO_MODEL,
    )
    db_session.add(agent)
    await db_session.flush()
    web_search = (
        await db_session.execute(select(Tool).where(Tool.name == "web_search"))
    ).scalar_one()
    db_session.add(AgentTool(agent_id=agent.id, tool_id=web_search.id))
    await db_session.commit()

    await ensure_starter_agents_chat_safe_tools(db_session)

    assigned = list(
        (
            await db_session.execute(
                select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id)
            )
        ).scalars()
    )
    assert assigned == [web_search.id]


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


async def test_seed_starter_assistant_is_orchestrator(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)
    assistant = (
        await db_session.execute(select(Agent).where(Agent.name == "Assistant"))
    ).scalar_one()
    assert assistant.instructions == _ASSISTANT_ORCHESTRATOR_INSTRUCTIONS


async def test_ensure_assistant_orchestrator_upgrades_legacy_prompt(
    db_session: AsyncSession,
) -> None:
    assistant = Agent(
        name="Assistant",
        description="A generalist assistant for everyday questions, brainstorming, planning, and "
        "quick tasks that don't need a specialist.",
        instructions=_LEGACY_ASSISTANT_INSTRUCTIONS,
        model=AUTO_MODEL,
    )
    db_session.add(assistant)
    await db_session.commit()

    await ensure_assistant_orchestrator_instructions(db_session)
    await db_session.refresh(assistant)
    assert assistant.instructions == _ASSISTANT_ORCHESTRATOR_INSTRUCTIONS


async def test_ensure_assistant_orchestrator_upgrades_previous_prompt(
    db_session: AsyncSession,
) -> None:
    assistant = Agent(
        name="Assistant",
        description="The channel orchestrator — always present, gathers context from the human, "
        "and decides when the rest of the team should join.",
        instructions=_PREVIOUS_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS,
        model=AUTO_MODEL,
    )
    db_session.add(assistant)
    await db_session.commit()

    await ensure_assistant_orchestrator_instructions(db_session)
    await db_session.refresh(assistant)
    assert assistant.instructions == _ASSISTANT_ORCHESTRATOR_INSTRUCTIONS


async def test_ensure_assistant_orchestrator_leaves_custom_prompt(
    db_session: AsyncSession,
) -> None:
    assistant = Agent(
        name="Assistant",
        description="Mine.",
        instructions="Do not change this.",
        model=AUTO_MODEL,
    )
    db_session.add(assistant)
    await db_session.commit()

    await ensure_assistant_orchestrator_instructions(db_session)
    await db_session.refresh(assistant)
    assert assistant.instructions == "Do not change this."


async def test_seed_starter_assistant_gets_always_rule(db_session: AsyncSession) -> None:
    """#406: everyday chat in a routed channel should reach Assistant."""
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    assistant = (
        await db_session.execute(select(Agent).where(Agent.name == "Assistant"))
    ).scalar_one()
    rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == assistant.id)
            )
        ).scalars()
    )
    assert [(rule.rule_type, rule.pattern) for rule in rules] == [("always", "")]


async def test_ensure_assistant_always_rule_backfills_keyword_assistant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    assistant = Agent(
        name="Assistant",
        description="A generalist.",
        instructions="Help.",
        model=AUTO_MODEL,
    )
    db_session.add(assistant)
    await db_session.flush()
    db_session.add(
        AgentRoutingRule(
            agent_id=assistant.id,
            rule_type="keyword",
            pattern='["specialist", "expert"]',
            priority=5,
        )
    )
    await db_session.commit()

    await ensure_assistant_always_rule(db_session)

    rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == assistant.id)
            )
        ).scalars()
    )
    assert any(rule.rule_type == "always" for rule in rules)
    assert any(rule.rule_type == "keyword" for rule in rules)


async def test_ensure_assistant_always_rule_leaves_mention_only_alone(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    assistant = Agent(
        name="Assistant",
        description="A generalist.",
        instructions="Help.",
        model=AUTO_MODEL,
    )
    db_session.add(assistant)
    await db_session.flush()
    db_session.add(
        AgentRoutingRule(agent_id=assistant.id, rule_type="mention_only", pattern="", priority=10)
    )
    await db_session.commit()

    await ensure_assistant_always_rule(db_session)

    rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == assistant.id)
            )
        ).scalars()
    )
    assert [(rule.rule_type, rule.pattern) for rule in rules] == [("mention_only", "")]


async def test_seed_starter_specialists_get_keyword_rules(db_session: AsyncSession) -> None:
    """#410: Writer/Researcher/Coder ship with real keywords, not a
    hidden LLM regex the sheet cannot show."""
    await seed_builtin_tools(db_session)
    await seed_starter_agents(db_session)

    for name in ("Writer", "Researcher", "Coder"):
        agent = (await db_session.execute(select(Agent).where(Agent.name == name))).scalar_one()
        rules = list(
            (
                await db_session.execute(
                    select(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent.id)
                )
            ).scalars()
        )
        assert [(rule.rule_type, rule.pattern, rule.priority) for rule in rules] == [
            starter_keyword_rule(name)
        ]


async def test_repair_drops_invalid_and_broad_regex_and_backfills(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    writer = Agent(
        name="Writer",
        description="Drafts prose.",
        instructions="Write well.",
        model=AUTO_MODEL,
    )
    researcher = Agent(
        name="Researcher",
        description="Finds things.",
        instructions="Search.",
        model=AUTO_MODEL,
    )
    db_session.add_all([writer, researcher])
    await db_session.flush()
    db_session.add(
        AgentRoutingRule(
            agent_id=writer.id,
            rule_type="regex",
            pattern=r"(?i)(\d{5}-\d{4}|[a-zA-Z]{2,}\s?\d{1,3})",
            priority=5,
        )
    )
    db_session.add(
        AgentRoutingRule(
            agent_id=researcher.id,
            rule_type="regex",
            pattern=r"\b(https?://[\w-]+(\.[\w-]+)+(\/[\w- ./?%&=]*)?)",
            priority=5,
        )
    )
    await db_session.commit()

    await repair_generated_routing_rules(db_session)

    writer_rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == writer.id)
            )
        ).scalars()
    )
    researcher_rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == researcher.id)
            )
        ).scalars()
    )
    assert [(rule.rule_type, rule.pattern, rule.priority) for rule in writer_rules] == [
        starter_keyword_rule("Writer")
    ]
    assert [(rule.rule_type, rule.pattern, rule.priority) for rule in researcher_rules] == [
        starter_keyword_rule("Researcher")
    ]


async def test_repair_leaves_mention_only_and_specific_regex_alone(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    writer = Agent(name="Writer", description="Drafts.", instructions="Write.", model=AUTO_MODEL)
    tickets = Agent(
        name="Tickets", description="Tracks tickets.", instructions="Track.", model=AUTO_MODEL
    )
    db_session.add_all([writer, tickets])
    await db_session.flush()
    db_session.add(
        AgentRoutingRule(agent_id=writer.id, rule_type="mention_only", pattern="", priority=10)
    )
    db_session.add(
        AgentRoutingRule(agent_id=tickets.id, rule_type="regex", pattern=r"ORD-\d+", priority=8)
    )
    await db_session.commit()

    await repair_generated_routing_rules(db_session)

    writer_rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == writer.id)
            )
        ).scalars()
    )
    ticket_rules = list(
        (
            await db_session.execute(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == tickets.id)
            )
        ).scalars()
    )
    assert [(rule.rule_type, rule.pattern) for rule in writer_rules] == [("mention_only", "")]
    assert [(rule.rule_type, rule.pattern) for rule in ticket_rules] == [("regex", r"ORD-\d+")]
