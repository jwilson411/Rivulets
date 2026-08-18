"""Starter agent/team library (#16): a fresh workspace ships with zero
agents and zero teams today, so this seeds a small, genuinely useful
default roster the first time a workspace is created — fully
editable/deletable afterward, exactly like anything a user builds by
hand (no "locked-in default" flag exists or is needed).

Mirrors tool_resolution.py's seed_builtin_tools() idempotent-by-name
pattern, but is called once, from api/auth.py's login() at the moment
the `workspace` row is first created — there's exactly one workspace per
install (see auth.py's module docstring), so "seed on first workspace
creation" and "seed once" are the same event here, unlike
seed_builtin_tools() which re-runs every startup.

Every starter agent uses AUTO_MODEL (#23) rather than a hardcoded
provider:model string: a brand-new workspace has no ProviderConfig yet
(that's set up in Settings > Providers, after this first login), and
Auto mode is the one `model` value that doesn't require one to exist at
agent-creation time — it just resolves lazily per-message once a
provider is configured. Until then these agents sit in the same
"provider unresolved" state a manually created agent would (see
agentos/models.py's UnknownProviderError, swallowed per-agent by
sync_agents()), which is expected and fine.

#406: Assistant is seeded with an `always` routing rule so everyday
channel chat ("How are you all doing today?") is answered. Dispatch
treats `always` as a human-turn rule — the speaker is not re-matched
on their own reply, so this does not become a self-loop.
#410: specialists get a few real keywords at seed time (not an LLM
catch-all or an invalid URL regex) so the sheet can show them.

Every starter agent is granted every builtin tool and every capability
scope (TOOL_SCOPES). Assignment without the matching AgentToolScope
leaves scoped tools inert (tool_resolution.py's resolve_agent_tools),
so the seed has to do both or the roster would advertise tools that
never resolve. Existing workspaces that still have the original curated
tool set (or Writer's empty set) are upgraded once via
ensure_starter_agents_have_all_tools; an owner who already changed
that set is left alone.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import AUTO_MODEL
from rivulets.agentos.tool_scopes import TOOL_SCOPES
from rivulets.db.models import (
    Agent,
    AgentRoutingRule,
    AgentTool,
    AgentToolScope,
    Team,
    TeamAgent,
    Tool,
)
from rivulets.sync.publish import publish_current_state, publish_tombstone


@dataclass(frozen=True)
class _StarterAgent:
    name: str
    description: str
    instructions: str


_ASSISTANT_NAME = "Assistant"
# Kept so ensure_assistant_orchestrator_instructions can upgrade workspaces
# still running the original generalist prompt without overwriting a
# human-edited one.
_LEGACY_ASSISTANT_INSTRUCTIONS = (
    "You are a helpful, general-purpose assistant. Answer clearly and directly, ask "
    "a clarifying question when the request is ambiguous, and hand off to a "
    "specialist teammate (Coder, Researcher, Writer) when their focus fits better."
)
_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS = (
    "You are the orchestrator for this channel. You are always present, even when "
    "the human did not add you to the team. The rest of the team stays quiet until "
    "you engage them or the human @mentions someone.\n"
    "\n"
    "Your job:\n"
    "- Answer everyday questions yourself when you can.\n"
    "- Ask one clarifying question when the request is ambiguous. Do not engage "
    "the team while you are still waiting on that answer.\n"
    "- When the request is clear and a specialist (Coder, Researcher, Writer, or "
    "another teammate) should take it, call engage_team with a short reason, or "
    "handoff to that teammate by name with the context they need.\n"
    "- Never pretend you lack the conversation so far — you are given the channel "
    "history with every turn."
)
_ASSISTANT_ORCHESTRATOR_DESCRIPTION = (
    "The channel orchestrator — always present, gathers context from the human, "
    "and decides when the rest of the team should join."
)
_LEGACY_ASSISTANT_DESCRIPTION = (
    "A generalist assistant for everyday questions, brainstorming, planning, and "
    "quick tasks that don't need a specialist."
)


_STARTER_AGENTS: tuple[_StarterAgent, ...] = (
    _StarterAgent(
        name="Assistant",
        description=_ASSISTANT_ORCHESTRATOR_DESCRIPTION,
        instructions=_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS,
    ),
    _StarterAgent(
        name="Coder",
        description=(
            "A coding and development-focused agent for reading, writing, and running code, "
            "debugging, and explaining technical implementation details."
        ),
        instructions=(
            "You are a coding assistant. Read and write files, run Python to verify your "
            "work, and explain your reasoning concisely. Prefer small, correct changes over "
            "speculative rewrites."
        ),
    ),
    _StarterAgent(
        name="Researcher",
        description=(
            "A research agent that searches the web and summarizes findings with sources, "
            "for questions that need current or external information."
        ),
        instructions=(
            "You are a research assistant. Use web search to find current, relevant "
            "information, cite what you find, and summarize it accurately rather than "
            "guessing from memory."
        ),
    ),
    _StarterAgent(
        name="Writer",
        description=(
            "A writing and editing agent for drafting, tightening, and proofreading prose — "
            "emails, docs, copy, and long-form text."
        ),
        instructions=(
            "You are a writing and editing assistant. Draft clean, well-structured prose, "
            "and when editing existing text, preserve the author's voice and intent while "
            "fixing clarity, grammar, and structure."
        ),
    ),
)

# Pre-all-tools seed assignments. Used only to recognize a starter that
# has never been customized, so the login repair can upgrade it without
# undoing an owner's later unchecks.
_LEGACY_STARTER_TOOL_NAMES: dict[str, frozenset[str]] = {
    "Assistant": frozenset({"web_search", "read_attached_file", "search_knowledge_base"}),
    "Coder": frozenset({"read_file", "write_file", "list_files", "execute_python"}),
    "Researcher": frozenset({"web_search", "http_request"}),
    "Writer": frozenset(),
}

_STARTER_TEAM_NAME = "Starter Team"
_STARTER_TEAM_DESCRIPTION = (
    "The default agent roster seeded on workspace creation — edit or replace freely."
)
# #406: everyday chat in a routed channel is supposed to get an answer
# without an @mention. The generalist is the one teammate that should
# take those messages; specialists get curated keywords (#410).
_ASSISTANT_ALWAYS_RULE: tuple[str, str, int] = ("always", "", 0)
_MATCHING_RULE_TYPES = frozenset({"keyword", "regex", "semantic", "always"})


def _starter_routing_rule(name: str) -> tuple[str, str, int] | None:
    if name == _ASSISTANT_NAME:
        return _ASSISTANT_ALWAYS_RULE
    # Lazy: dispatch/__init__.py pulls service.py; don't import that at
    # module load (same cycle agent_lifecycle.py already dodges).
    from rivulets.dispatch.rule_generation import starter_keyword_rule

    return starter_keyword_rule(name)


async def seed_starter_agents(db: AsyncSession) -> None:
    """Idempotent by name. Assumes seed_builtin_tools() has already run
    (app.py's lifespan calls it on every startup, which always happens
    before login can) so the builtin Tool rows referenced below exist."""
    result = await db.execute(select(Agent.name))
    existing_names = set(result.scalars().all())

    for starter in _STARTER_AGENTS:
        if starter.name in existing_names:
            continue
        agent = Agent(
            name=starter.name,
            description=starter.description,
            instructions=starter.instructions,
            model=AUTO_MODEL,
        )
        db.add(agent)
        await db.flush()
        seeded = _starter_routing_rule(starter.name)
        if seeded is not None:
            rule_type, pattern, priority = seeded
            db.add(
                AgentRoutingRule(
                    agent_id=agent.id,
                    rule_type=rule_type,
                    pattern=pattern,
                    priority=priority,
                )
            )

    await db.commit()
    created = [starter.name for starter in _STARTER_AGENTS if starter.name not in existing_names]
    if created:
        await _grant_all_tools_to_named_agents(db, created)
    await ensure_starter_agents_have_all_tools(db)
    await ensure_assistant_always_rule(db)
    await ensure_assistant_orchestrator_instructions(db)
    await repair_generated_routing_rules(db)


async def _grant_all_tools_to_named_agents(db: AsyncSession, names: list[str]) -> None:
    tool_result = await db.execute(select(Tool).where(Tool.tool_type == "builtin"))
    builtin_ids = {row.id for row in tool_result.scalars().all()}
    if not builtin_ids or not names:
        return
    agents = list((await db.scalars(select(Agent).where(Agent.name.in_(tuple(names))))).all())
    await _grant_all_tools(db, agents, builtin_ids)


async def _grant_all_tools(
    db: AsyncSession, agents: list[Agent], builtin_ids: set[str]
) -> None:
    changed = False
    for agent in agents:
        existing_tool_ids = set(
            (
                await db.scalars(select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id))
            ).all()
        )
        missing_tool_ids = builtin_ids - existing_tool_ids
        for tool_id in missing_tool_ids:
            db.add(AgentTool(agent_id=agent.id, tool_id=tool_id))

        existing_scopes = set(
            (
                await db.scalars(
                    select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
                )
            ).all()
        )
        missing_scopes = TOOL_SCOPES - existing_scopes
        for scope in missing_scopes:
            db.add(AgentToolScope(agent_id=agent.id, scope=scope))

        if missing_tool_ids or missing_scopes:
            changed = True

    if changed:
        await db.commit()


async def ensure_starter_agents_have_all_tools(db: AsyncSession) -> None:
    """Upgrade a starter that still has its original curated tool set
    (or Writer's empty set) to every builtin + every scope. A starter
    whose assignment already differs -- owner unchecked something, or
    already on the full set -- is left alone. Idempotent."""
    tool_result = await db.execute(select(Tool).where(Tool.tool_type == "builtin"))
    builtin_rows = list(tool_result.scalars().all())
    builtin_ids = {row.id for row in builtin_rows}
    name_by_id = {row.id: row.name for row in builtin_rows}
    if not builtin_ids:
        return

    starter_names = tuple(starter.name for starter in _STARTER_AGENTS)
    agents = list((await db.scalars(select(Agent).where(Agent.name.in_(starter_names)))).all())
    to_upgrade: list[Agent] = []
    for agent in agents:
        existing_tool_ids = set(
            (
                await db.scalars(select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id))
            ).all()
        )
        assigned_names = frozenset(
            name_by_id[tool_id] for tool_id in existing_tool_ids if tool_id in name_by_id
        )
        legacy = _LEGACY_STARTER_TOOL_NAMES.get(agent.name)
        if legacy is not None and assigned_names == legacy:
            to_upgrade.append(agent)
    await _grant_all_tools(db, to_upgrade, builtin_ids)


async def ensure_assistant_always_rule(db: AsyncSession) -> None:
    """#406: workspaces created before Assistant shipped with `always`
    still have the generated specialist-keyword rule (or no rule). Add
    `always` unless the owner already opted that agent into mention-only.
    Idempotent. Publishes the new row so a peer sees the same routing."""
    assistant = await db.scalar(select(Agent).where(Agent.name == _ASSISTANT_NAME))
    if assistant is None:
        return
    rules = list(
        (
            await db.scalars(
                select(AgentRoutingRule).where(AgentRoutingRule.agent_id == assistant.id)
            )
        ).all()
    )
    if any(rule.rule_type == "mention_only" for rule in rules):
        return
    if any(rule.rule_type == "always" for rule in rules):
        return
    rule_type, pattern, priority = _ASSISTANT_ALWAYS_RULE
    row = AgentRoutingRule(
        agent_id=assistant.id, rule_type=rule_type, pattern=pattern, priority=priority
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await publish_current_state(db, "agent_routing_rule", row.id)


async def ensure_assistant_orchestrator_instructions(db: AsyncSession) -> None:
    """Upgrade the seeded Assistant prompt to the orchestrator wording
    when the owner has not customized it. Idempotent. Does not touch a
    rewritten description or instructions."""
    assistant = await db.scalar(select(Agent).where(Agent.name == _ASSISTANT_NAME))
    if assistant is None:
        return
    changed = False
    if assistant.instructions == _LEGACY_ASSISTANT_INSTRUCTIONS:
        assistant.instructions = _ASSISTANT_ORCHESTRATOR_INSTRUCTIONS
        assistant.vector_clock += 1
        changed = True
    if assistant.description == _LEGACY_ASSISTANT_DESCRIPTION:
        assistant.description = _ASSISTANT_ORCHESTRATOR_DESCRIPTION
        if not changed:
            assistant.vector_clock += 1
        changed = True
    if not changed:
        return
    await db.commit()
    await publish_current_state(db, "agent", assistant.id)


async def repair_generated_routing_rules(db: AsyncSession) -> None:
    """#410: drop invalid or catch-all regex rows left by earlier
    generators. If a starter specialist then has nothing that can match,
    give it the curated keywords the sheet can show. Leaves mention-only
    and always (and any remaining useful rules) alone. Idempotent."""
    from rivulets.dispatch.rule_generation import starter_keyword_rule
    from rivulets.dispatch.rules import is_overly_broad_regex, is_valid_regex

    agents = list((await db.scalars(select(Agent))).all())
    tombstone_ids: list[str] = []
    added_rows: list[AgentRoutingRule] = []
    for agent in agents:
        rules = list(
            (
                await db.scalars(
                    select(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent.id)
                )
            ).all()
        )
        surviving: list[AgentRoutingRule] = []
        for rule in rules:
            if rule.rule_type == "regex" and (
                not is_valid_regex(rule.pattern) or is_overly_broad_regex(rule.pattern)
            ):
                tombstone_ids.append(rule.id)
                await db.delete(rule)
                continue
            surviving.append(rule)
        if any(rule.rule_type == "mention_only" for rule in surviving):
            continue
        if any(rule.rule_type in _MATCHING_RULE_TYPES for rule in surviving):
            continue
        curated = starter_keyword_rule(agent.name)
        if curated is None:
            continue
        rule_type, pattern, priority = curated
        row = AgentRoutingRule(
            agent_id=agent.id, rule_type=rule_type, pattern=pattern, priority=priority
        )
        db.add(row)
        added_rows.append(row)
    if not tombstone_ids and not added_rows:
        return
    await db.commit()
    for row in added_rows:
        await db.refresh(row)
        await publish_current_state(db, "agent_routing_rule", row.id)
    for old_id in tombstone_ids:
        await publish_tombstone(db, "agent_routing_rule", old_id)


async def seed_starter_teams(db: AsyncSession) -> None:
    """Idempotent by name. Assumes seed_starter_agents() already ran (in
    the same login() call) so the agents it groups already exist."""
    existing = await db.scalar(select(Team).where(Team.name == _STARTER_TEAM_NAME))
    if existing is not None:
        return

    agent_result = await db.execute(
        select(Agent).where(Agent.name.in_(tuple(starter.name for starter in _STARTER_AGENTS)))
    )
    agents_by_name = {row.name: row for row in agent_result.scalars().all()}

    team = Team(name=_STARTER_TEAM_NAME, description=_STARTER_TEAM_DESCRIPTION)
    db.add(team)
    await db.flush()  # populate team.id before referencing it in TeamAgent rows

    for position, starter in enumerate(_STARTER_AGENTS):
        agent = agents_by_name.get(starter.name)
        if agent is not None:
            db.add(TeamAgent(team_id=team.id, agent_id=agent.id, position=position))

    await db.commit()
