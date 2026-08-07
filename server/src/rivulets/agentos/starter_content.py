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
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import AUTO_MODEL
from rivulets.db.models import Agent, AgentTool, Team, TeamAgent, Tool


@dataclass(frozen=True)
class _StarterAgent:
    name: str
    description: str
    instructions: str
    tool_names: tuple[str, ...] = ()


_STARTER_AGENTS: tuple[_StarterAgent, ...] = (
    _StarterAgent(
        name="Assistant",
        description=(
            "A generalist assistant for everyday questions, brainstorming, planning, and "
            "quick tasks that don't need a specialist."
        ),
        instructions=(
            "You are a helpful, general-purpose assistant. Answer clearly and directly, ask "
            "a clarifying question when the request is ambiguous, and hand off to a "
            "specialist teammate (Coder, Researcher, Writer) when their focus fits better."
        ),
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
        tool_names=("read_file", "write_file", "list_files", "execute_python"),
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
        tool_names=("web_search", "http_request"),
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

_STARTER_TEAM_NAME = "Starter Team"
_STARTER_TEAM_DESCRIPTION = (
    "The default agent roster seeded on workspace creation — edit or replace freely."
)


async def seed_starter_agents(db: AsyncSession) -> None:
    """Idempotent by name. Assumes seed_builtin_tools() has already run
    (app.py's lifespan calls it on every startup, which always happens
    before login can) so the builtin Tool rows referenced below exist."""
    result = await db.execute(select(Agent.name))
    existing_names = set(result.scalars().all())

    tool_result = await db.execute(select(Tool).where(Tool.tool_type == "builtin"))
    tool_ids_by_name = {row.name: row.id for row in tool_result.scalars().all()}

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
        await db.flush()  # populate agent.id before referencing it in AgentTool rows
        for tool_name in starter.tool_names:
            tool_id = tool_ids_by_name.get(tool_name)
            if tool_id is not None:
                db.add(AgentTool(agent_id=agent.id, tool_id=tool_id))

    await db.commit()


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
