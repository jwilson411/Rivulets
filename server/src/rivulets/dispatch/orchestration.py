"""Assistant-as-orchestrator: always present, team locked until engaged.

A new conversation starts with only the workspace Assistant speaking.
Specialists stay quiet so a clarifying question is not drowned out by
keyword matches and rematches. The human, or Assistant via the
`engage_team` / `handoff` tools, unlocks the rest of the roster.

If there is no agent named Assistant, dispatch is unchanged — teams
without an orchestrator keep the old everyone-who-matches behavior.
"""

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Agent, Message
from rivulets.dispatch.engine import AgentDispatchInfo, DispatchMethod, DispatchResult
from rivulets.dispatch.rules import Rule, RuleType

ORCHESTRATOR_NAME = "Assistant"
TEAM_ENGAGED_CONTENT_TYPE = "team_engaged"


def is_orchestrator_name(name: str) -> bool:
    return name.lower() == ORCHESTRATOR_NAME.lower()


def orchestrator_dispatch_info(agent: Agent) -> AgentDispatchInfo:
    """Force always-on for dispatch regardless of the owner's When to
    speak setting. Mention-only on Assistant would silently drop the
    only teammate allowed to ask clarifying questions."""
    return AgentDispatchInfo(
        agent_id=agent.id,
        name=agent.name,
        rules=[Rule(RuleType.ALWAYS)],
        description=agent.description,
    )


async def load_orchestrator(db: AsyncSession) -> tuple[Agent, AgentDispatchInfo] | None:
    agent = await db.scalar(select(Agent).where(Agent.name == ORCHESTRATOR_NAME))
    if agent is None:
        return None
    return agent, orchestrator_dispatch_info(agent)


def find_orchestrator_id(agents: list[tuple[Agent, AgentDispatchInfo]]) -> str | None:
    for agent, _info in agents:
        if is_orchestrator_name(agent.name):
            return agent.id
    return None


async def is_team_engaged(db: AsyncSession, rivulet_id: str) -> bool:
    """True once this thread has an engage or handoff message.

    Derived from synced Message rows so a peer that continues the
    conversation sees the same lock state — no extra column.
    """
    return bool(
        await db.scalar(
            select(
                exists().where(
                    Message.rivulet_id == rivulet_id,
                    or_(
                        Message.content_type == TEAM_ENGAGED_CONTENT_TYPE,
                        Message.content_type == "handoff",
                    ),
                )
            )
        )
    )


def apply_orchestrator_lock(
    result: DispatchResult,
    *,
    team_engaged: bool,
    from_agent_id: str | None,
    orchestrator_id: str | None,
) -> DispatchResult:
    """While locked, only @mentions and the orchestrator (on a human
    turn) may run. No orchestrator means there is nothing to lock."""
    if team_engaged or orchestrator_id is None:
        return result
    if result.method is DispatchMethod.MENTION:
        return result
    if from_agent_id is None:
        return DispatchResult(
            agent_ids=[orchestrator_id],
            method=DispatchMethod.DEFAULT,
            llm_invoked=result.llm_invoked,
        )
    return DispatchResult(
        agent_ids=[],
        method=DispatchMethod.NONE,
        llm_invoked=result.llm_invoked,
    )


def merge_orchestrator(
    team_agents: list[tuple[Agent, AgentDispatchInfo]],
    orchestrator: tuple[Agent, AgentDispatchInfo] | None,
) -> list[tuple[Agent, AgentDispatchInfo]]:
    """Put Assistant on the roster even when they are not on the team,
    and replace a stored mention-only/keyword rule with always."""
    if orchestrator is None:
        return team_agents
    orch_agent, orch_info = orchestrator
    merged: list[tuple[Agent, AgentDispatchInfo]] = []
    seen = False
    for agent, info in team_agents:
        if agent.id == orch_agent.id:
            merged.append((orch_agent, orch_info))
            seen = True
        else:
            merged.append((agent, info))
    if not seen:
        merged.insert(0, (orch_agent, orch_info))
    return merged
