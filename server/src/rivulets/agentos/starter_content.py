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

Starter agents ship with a chat-safe tool kit (search, attachments,
knowledge-base lookup; Coder also gets unscoped file reads) and no
capability scopes (#462). Handoff / engage_team attach unconditionally
in agentos/service.py, so they are not part of this assignment.
set_working_directory, execute_python, Google write, workspace
settings, and invites stay off until the owner grants them on that
agent. A #459 seed that checked every builtin and every scope is
retracted on the next login via ensure_starter_agents_chat_safe_tools;
a starter the owner already customized is left alone.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.agent_lifecycle import (
    publish_agent_tool_scopes_change,
    publish_agent_tools_change,
    set_agent_tool_scopes,
    set_agent_tools,
)
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
_PREVIOUS_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS = (
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
_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS = (
    "You are the orchestrator for this channel. You are always present, even when "
    "the human did not add you to the team. Specialists stay quiet until you hand "
    "off to them or the human @mentions someone. Do not unlock the whole roster "
    "and hope the right person speaks.\n"
    "\n"
    "Your job:\n"
    "- Answer everyday questions yourself when you can.\n"
    "- Ask one clarifying question when the request is ambiguous. Do not hand "
    "off while you are still waiting on that answer.\n"
    "- When a specialist on the team should take the work, call handoff with "
    "their name and the context they need. Pick exactly one teammate.\n"
    "- After a specialist replies, decide the next step: summarize for the "
    "human, hand off to a *different* specialist, or ask the human a question. "
    "Do not bounce the same specialist unless they still have unfinished work.\n"
    "- If the work needs a role that is not on the team (for example a DBA), "
    "tell the human who you would hire and why, and wait for them to agree. "
    "After they agree, call hire_teammate and then handoff to that same name "
    "in the same turn so they start immediately.\n"
    "- Never pretend you lack the conversation so far — you are given the "
    "channel history and the current team roster with every turn."
)
_ASSISTANT_ORCHESTRATOR_DESCRIPTION = (
    "The channel orchestrator — always present, hands work to one specialist "
    "at a time, and can hire a missing role after the human agrees."
)
_PREVIOUS_ASSISTANT_ORCHESTRATOR_DESCRIPTION = (
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

# Chat-safe defaults (#462). No required_scope, no sensitive blast-radius
# tools. Coder keeps unscoped reads so the role can inspect a folder the
# owner already pointed the channel at; write/exec stay off.
_CHAT_SAFE_STARTER_TOOLS: frozenset[str] = frozenset(
    {"web_search", "read_attached_file", "search_knowledge_base"}
)
_STARTER_TOOL_NAMES: dict[str, frozenset[str]] = {
    "Assistant": _CHAT_SAFE_STARTER_TOOLS,
    "Coder": frozenset({"read_file", "list_files", "read_attached_file"}),
    "Researcher": _CHAT_SAFE_STARTER_TOOLS,
    "Writer": frozenset({"read_attached_file", "search_knowledge_base"}),
}
# Signature of the #459 "every tool + every scope" seed. Matching this
# (not "differs from chat-safe") is what the login repair retracts, so a
# later builtin does not hide the hole and an owner who only granted
# execute_python is not reset.
_UNRESTRICTED_STARTER_MARKERS: frozenset[str] = frozenset(
    {
        "set_working_directory",
        "create_invite",
        "update_workspace_settings",
        "execute_python",
        "google_gmail_send",
    }
)

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
        await _grant_chat_safe_tools_to_named_agents(db, created)
    await ensure_starter_agents_chat_safe_tools(db)
    await ensure_assistant_always_rule(db)
    await ensure_assistant_orchestrator_instructions(db)
    await repair_generated_routing_rules(db)


async def _grant_chat_safe_tools_to_named_agents(db: AsyncSession, names: list[str]) -> None:
    tool_result = await db.execute(select(Tool).where(Tool.tool_type == "builtin"))
    id_by_name = {row.name: row.id for row in tool_result.scalars().all()}
    if not id_by_name or not names:
        return
    agents = list((await db.scalars(select(Agent).where(Agent.name.in_(tuple(names))))).all())
    changed = False
    for agent in agents:
        desired_ids = {
            id_by_name[name] for name in _STARTER_TOOL_NAMES[agent.name] if name in id_by_name
        }
        existing_tool_ids = set(
            (
                await db.scalars(select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id))
            ).all()
        )
        for tool_id in desired_ids - existing_tool_ids:
            db.add(AgentTool(agent_id=agent.id, tool_id=tool_id))
            changed = True
    if changed:
        await db.commit()


async def ensure_starter_agents_chat_safe_tools(db: AsyncSession) -> None:
    """Retract a starter that still has the #459 every-tool + every-scope
    grant. A starter whose assignment already differs -- owner unchecked
    something, granted a subset, or still on the pre-#459 curated set --
    is left alone. Idempotent. Publishes the join-row diff so a peer
    drops the same grant rather than syncing it back."""
    tool_result = await db.execute(select(Tool).where(Tool.tool_type == "builtin"))
    builtin_rows = list(tool_result.scalars().all())
    id_by_name = {row.name: row.id for row in builtin_rows}
    name_by_id = {row.id: row.name for row in builtin_rows}
    if not id_by_name:
        return

    starter_names = tuple(starter.name for starter in _STARTER_AGENTS)
    agents = list((await db.scalars(select(Agent).where(Agent.name.in_(starter_names)))).all())
    published: list[tuple[str, set[str], set[str], set[str], set[str]]] = []
    for agent in agents:
        existing_tool_ids = set(
            (
                await db.scalars(select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id))
            ).all()
        )
        assigned_names = frozenset(
            name_by_id[tool_id] for tool_id in existing_tool_ids if tool_id in name_by_id
        )
        existing_scopes = set(
            (
                await db.scalars(
                    select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
                )
            ).all()
        )
        if existing_scopes != set(TOOL_SCOPES):
            continue
        if not _UNRESTRICTED_STARTER_MARKERS <= assigned_names:
            continue
        desired = _STARTER_TOOL_NAMES[agent.name]
        desired_ids = [id_by_name[name] for name in sorted(desired) if name in id_by_name]
        tool_diff = await set_agent_tools(db, agent.id, desired_ids)
        scope_diff = await set_agent_tool_scopes(db, agent.id, [])
        published.append((agent.id, tool_diff[0], tool_diff[1], scope_diff[0], scope_diff[1]))

    if not published:
        return
    await db.commit()
    for agent_id, old_tools, new_tools, old_scopes, new_scopes in published:
        await publish_agent_tools_change(db, agent_id, old_tools, new_tools)
        await publish_agent_tool_scopes_change(db, agent_id, old_scopes, new_scopes)


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
    if assistant.instructions in {
        _LEGACY_ASSISTANT_INSTRUCTIONS,
        _PREVIOUS_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS,
    }:
        assistant.instructions = _ASSISTANT_ORCHESTRATOR_INSTRUCTIONS
        assistant.vector_clock += 1
        changed = True
    if assistant.description in {
        _LEGACY_ASSISTANT_DESCRIPTION,
        _PREVIOUS_ASSISTANT_ORCHESTRATOR_DESCRIPTION,
    }:
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
