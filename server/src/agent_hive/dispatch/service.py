"""Bridges the pure DispatchEngine (dispatch/engine.py, DB-free) to our DB
and to AgentOS: loads a channel team's agents + routing rules, runs the
dispatcher, invokes matched agents, and persists their replies as thread
messages (FR-4.1, FR-5.2, FR-6.1's precursor, FR-12.1).

Also recurses: an agent's own reply is itself re-dispatched (FR-5.6,
AC-014's "Architect mentions @DBA, DBA responds" scenario), which is what
gives loop-prevention guards (FR-7, dispatch/guards.py) something to
actually guard against — without recursion, one human message can only
ever produce one flat round of replies and a loop is structurally
impossible. Recursion depth is bounded by the guard checks running before
each invocation, not by a separate depth counter: worst case is
~guard.turn_limit calls deep, comfortably under Python's recursion limit
for the FR-7.4-documented range (1-100).

No LLM fallback is wired in yet (ADR-005's stage 2) — DispatchEngine is
constructed without one, so only @mentions and manually-set deterministic
rules can trigger a response today. Agent-generated rules (FR-3.3) cover
the manual-setup gap on creation/edit, but there's still no semantic
fallback for messages that miss every rule.
"""

import json
import logging

from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.agentos import run_agent
from agent_hive.db.models import Agent, AgentRoutingRule, Channel, Message, TeamAgent, Thread
from agent_hive.dispatch.engine import AgentDispatchInfo, DispatchEngine
from agent_hive.dispatch.guards import (
    get_or_create_guard_state,
    record_agent_message,
    reset_guard_state,
)
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
    db: AsyncSession,
    thread: Thread,
    channel: Channel,
    message_content: str,
    *,
    from_agent_id: str | None = None,
    from_agent_name: str | None = None,
) -> list[Message]:
    """Run the dispatcher against `channel`'s team and invoke every matched
    agent, appending its reply to `thread` as a Message row. Returns the
    new Message rows (already added to `db`, not yet committed — callers
    own the commit so this composes with whatever else they're persisting
    in the same request).

    `from_agent_id`/`from_agent_name` are set only on recursive calls
    triggered by another agent's own message — omit them for the normal,
    human-triggered path.
    """
    guard_state = await get_or_create_guard_state(db, thread.id)
    if from_agent_id is None:
        reset_guard_state(guard_state)  # FR-7.5: a human message always resumes
    elif guard_state.paused:
        return []  # FR-7.1/7.2/7.3: silent until a human reactivates

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
        if guard_state.paused:
            # An earlier agent in this same round (or a deeper recursive
            # call sharing this guard_state) just tripped a guard.
            break

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
            # it's not something the agent "said".
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
            db.add(message)
            new_messages.append(message)
            continue  # provider errors don't count toward guard limits or recurse

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

        pause_message = await record_agent_message(
            db,
            thread.id,
            guard_state,
            from_agent_id=from_agent_id,
            from_agent_name=from_agent_name or "",
            to_agent_id=agent.id,
            to_agent_name=agent.name,
        )
        if pause_message is not None:
            thread.status = "paused"
            db.add(pause_message)
            new_messages.append(pause_message)
            break

        # FR-5.6/AC-014: this agent's own message can itself trigger a
        # teammate (e.g. an @mention in its reply) — recurse.
        recursive_messages = await dispatch_and_respond(
            db, thread, channel, content, from_agent_id=agent.id, from_agent_name=agent.name
        )
        new_messages.extend(recursive_messages)

    return new_messages
