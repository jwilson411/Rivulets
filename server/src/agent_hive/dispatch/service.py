"""Bridges the pure DispatchEngine (dispatch/engine.py, DB-free) to our DB
and to AgentOS: loads a channel team's agents + routing rules, runs the
dispatcher, invokes matched agents, and persists their replies as thread
messages (FR-4.1, FR-5.2, FR-6.1's precursor, FR-12.1).

No LLM fallback is wired in yet (ADR-005's stage 2) — DispatchEngine is
constructed without one, so only @mentions and manually-set deterministic
rules can trigger a response today. Agent-generated rules (FR-3.3) aren't
built yet either, so a freshly created agent has none until someone PATCHes
`/agents/{id}/routing-rules` by hand.
"""

import json
import logging

from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.agentos import run_agent
from agent_hive.db.models import Agent, AgentRoutingRule, Channel, Message, TeamAgent, Thread
from agent_hive.dispatch.engine import AgentDispatchInfo, DispatchEngine
from agent_hive.dispatch.rules import Rule, RuleType

logger = logging.getLogger(__name__)

_ARRAY_PATTERN_TYPES = {RuleType.KEYWORD, RuleType.SEMANTIC}


def _row_to_rule(row: AgentRoutingRule) -> Rule:
    rule_type = RuleType(row.rule_type)
    pattern: list[str] | str
    if rule_type in _ARRAY_PATTERN_TYPES:
        pattern = json.loads(row.pattern) if row.pattern else []
    else:
        pattern = row.pattern
    return Rule(rule_type=rule_type, pattern=pattern, priority=row.priority)


async def _load_team_dispatch_agents(
    db: AsyncSession, team_id: str
) -> list[tuple[Agent, AgentDispatchInfo]]:
    result = await db.execute(
        select(Agent)
        .join(TeamAgent, TeamAgent.agent_id == Agent.id)
        .where(TeamAgent.team_id == team_id)
    )
    agents = result.scalars().all()

    pairs: list[tuple[Agent, AgentDispatchInfo]] = []
    for agent in agents:
        rules_result = await db.execute(
            select(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent.id)
        )
        rules = [_row_to_rule(row) for row in rules_result.scalars().all()]
        pairs.append((agent, AgentDispatchInfo(agent_id=agent.id, name=agent.name, rules=rules)))
    return pairs


async def dispatch_and_respond(
    db: AsyncSession, thread: Thread, channel: Channel, message_content: str
) -> list[Message]:
    """Run the dispatcher against `channel`'s team and invoke every matched
    agent, appending its reply to `thread` as a Message row. Returns the
    new Message rows (already added to `db`, not yet committed — callers
    own the commit so this composes with whatever else they're persisting
    in the same request).
    """
    if channel.team_id is None:
        return []

    team_agents = await _load_team_dispatch_agents(db, channel.team_id)
    if not team_agents:
        return []

    agent_by_id = {agent.id: agent for agent, _ in team_agents}
    dispatch_infos = [info for _, info in team_agents]

    engine = DispatchEngine()  # TODO(ADR-005 stage 2): inject an LLM fallback
    result = await engine.dispatch(message_content, dispatch_infos)

    if thread.agentos_session_id is None:
        thread.agentos_session_id = thread.id  # FR-12.2: one AgentOS session per thread

    new_messages: list[Message] = []
    for agent_id in result.agent_ids:
        agent = agent_by_id[agent_id]
        try:
            run_output = await run_agent(
                db,
                agent_id,
                message_content,
                session_id=thread.agentos_session_id,
                user_id="human",
            )
        except Exception:
            # NFR-2.4: one agent's provider being unreachable doesn't stop
            # others in the same dispatch from responding. Covers failures
            # in our own run_agent() (e.g. "not registered") that happen
            # before agno even gets a chance to run.
            logger.warning(
                "Agent %r failed to respond in thread %r", agent.name, thread.id, exc_info=True
            )
            continue

        if run_output.status is RunStatus.error:
            # Observed in practice: a bad API key doesn't raise — agno
            # catches the provider's HTTP error and returns a normal-looking
            # RunOutput whose `content` is the raw error string. Surfacing
            # that as if the agent said it would be confusing (NFR-5.4:
            # plain-language errors, not raw exception text) and wrong —
            # it's not something the agent "said". Post it as a system
            # message instead, same as a loop-guard pause (FR-7.1) does.
            logger.warning(
                "Agent %r run failed in thread %r: %s", agent.name, thread.id, run_output.content
            )
            message = Message(
                thread_id=thread.id,
                sender_type="system",
                sender_name="system",
                content=f"{agent.name} couldn't respond — its provider returned an error.",
                content_type="system_alert",
            )
        else:
            # get_content_as_string()'s **kwargs is Unknown in agno's own stubs.
            content = run_output.get_content_as_string() or ""  # pyright: ignore[reportUnknownMemberType]
            message = Message(
                thread_id=thread.id,
                sender_type="agent",
                sender_id=agent.id,
                sender_name=agent.name,
                content=content,
            )

        db.add(message)
        new_messages.append(message)

    return new_messages
