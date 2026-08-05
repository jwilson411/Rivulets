"""Bridges the pure DispatchEngine (dispatch/engine.py, DB-free) to our DB
and to AgentOS: loads a channel team's agents + routing rules, runs the
dispatcher, invokes matched agents, and persists their replies as thread
messages (FR-4.1, FR-5.2, FR-12.1).

Also recurses: an agent's own reply is itself re-dispatched (FR-5.6,
AC-014's "Architect mentions @DBA, DBA responds" scenario), which is what
gives loop-prevention guards (FR-7, dispatch/guards.py) something to
actually guard against — without recursion, one human message can only
ever produce one flat round of replies and a loop is structurally
impossible. Recursion depth is bounded by the guard checks running before
each invocation, not by a separate depth counter: worst case is
~guard.turn_limit calls deep, comfortably under Python's recursion limit
for the FR-7.4-documented range (1-100).

Handoffs (FR-6) reuse the exact same invoke/error/persist/guard/recurse
pipeline as ordinary dispatch — `_invoke_agent` and `_handle_handoff` call
each other: after any successful run, `_invoke_agent` checks the result
for a `handoff` tool call (tools/builtin/handoff.py) and, if present,
`_handle_handoff` posts the visible handoff message and calls
`_invoke_agent` again for the named target. A handoff is just a
directly-targeted invocation (bypassing routing rules, like @mention)
that happens to also carry a human-readable announcement — it goes
through the same guard bookkeeping as any other agent-to-agent step, so a
handoff ping-pong trips the same cycle detector a mention ping-pong would.

Publishes SSE events (FR-12.3, streaming.py) as it goes — `agent_token`
per streamed content delta, `agent_message` once a reply is persisted,
`handoff` when one occurs, `error`/`system_alert` on failure or guard
pause, and `done` once per external (non-recursive) call. Persisting rows
and publishing events both happen inline here, in the same request that
triggered the dispatch — see api/threads.py's SSE endpoint for how a
concurrent connection observes these live while this coroutine runs.

A message that misses every @mention and deterministic rule falls through
to dispatch/llm_fallback.py's LLM-based fallback (ADR-005 stage 2) before
being dropped as unrouted — see that module's docstring for model
selection and graceful-degradation behavior.
"""

import json
import logging

from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos import run_agent
from rivulets.db.models import (
    Agent,
    AgentRoutingRule,
    Channel,
    Message,
    TeamAgent,
    Thread,
    ThreadGuardState,
)
from rivulets.dispatch.engine import AgentDispatchInfo, DispatchEngine
from rivulets.dispatch.guards import (
    get_or_create_guard_state,
    record_agent_message,
    reset_guard_state,
)
from rivulets.dispatch.llm_fallback import build_llm_fallback
from rivulets.dispatch.rules import Rule, RuleType
from rivulets.streaming import publish

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
        pairs.append(
            (
                agent,
                AgentDispatchInfo(
                    agent_id=agent.id, name=agent.name, rules=rules, description=agent.description
                ),
            )
        )
    return pairs


def _find_handoff_call(run_output: RunOutput) -> tuple[str, str] | None:
    """Look for a `handoff(target_agent_name, context, ...)` call in a
    completed run's tool calls. Returns (target_agent_name, context) or
    None. Tool args come back as a plain dict from agno; a malformed or
    missing arg is treated as "no handoff" rather than an error — the
    calling agent's own reply already persisted regardless."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "handoff":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        target = args.get("target_agent_name")
        context = args.get("context")
        if isinstance(target, str) and isinstance(context, str):
            return target, context
    return None


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
    is_top_level = from_agent_id is None
    try:
        return await _dispatch_and_respond(
            db, thread, channel, message_content, from_agent_id, from_agent_name
        )
    finally:
        # One "no more events for this trigger" signal per external call,
        # regardless of which return path fired above (SSE clients need
        # this even when nothing ended up matching, api-design.md's `done`).
        if is_top_level:
            publish(thread.id, "done", {"thread_id": thread.id})


async def _dispatch_and_respond(
    db: AsyncSession,
    thread: Thread,
    channel: Channel,
    message_content: str,
    from_agent_id: str | None,
    from_agent_name: str | None,
) -> list[Message]:
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

    engine = DispatchEngine(llm_fallback=build_llm_fallback(db))
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
        new_messages.extend(
            await _invoke_agent(
                db,
                thread,
                channel,
                guard_state,
                agent,
                message_content,
                team_agents,
                from_agent_id=from_agent_id,
                from_agent_name=from_agent_name,
            )
        )

    return new_messages


async def _invoke_agent(
    db: AsyncSession,
    thread: Thread,
    channel: Channel,
    guard_state: ThreadGuardState,
    agent: Agent,
    message_content: str,
    team_agents: list[tuple[Agent, AgentDispatchInfo]],
    *,
    from_agent_id: str | None,
    from_agent_name: str | None,
) -> list[Message]:
    """Run one agent, persist its reply (or a failure notice), update
    guard state, then act on whatever the run implies: a handoff call
    (FR-6), a tripped guard (stop), or neither (recurse — FR-5.6). Shared
    by the main dispatch loop and _handle_handoff's target invocation,
    since both need the identical run/error/persist/guard/recurse pipeline.
    """
    seq = 0

    def on_token(delta: str, agent_id: str = agent.id, agent_name: str = agent.name) -> None:
        nonlocal seq
        seq += 1
        publish(
            thread.id,
            "agent_token",
            {"agent_id": agent_id, "agent_name": agent_name, "token": delta, "seq": seq},
        )

    assert thread.agentos_session_id is not None  # set by the top-level call before any agent runs
    try:
        run_output = await run_agent(
            db,
            agent.id,
            message_content,
            session_id=thread.agentos_session_id,
            user_id="human",
            on_token=on_token,
        )
    except Exception as exc:
        # NFR-2.4: one agent's provider being unreachable doesn't stop
        # others in the same dispatch from responding. Covers failures in
        # our own run_agent() (e.g. "not registered") that happen before
        # agno even gets a chance to run.
        logger.warning(
            "Agent %r failed to respond in thread %r", agent.name, thread.id, exc_info=True
        )
        publish(thread.id, "error", {"agent_id": agent.id, "error": str(exc)})
        return []

    if run_output.status is RunStatus.error:
        # Observed in practice: a bad API key doesn't raise — agno catches
        # the provider's HTTP error and returns a normal-looking RunOutput
        # whose `content` is the raw error string. Surfacing that as if
        # the agent said it would be confusing (NFR-5.4: plain-language
        # errors, not raw exception text) and wrong — it's not something
        # the agent "said".
        logger.warning(
            "Agent %r run failed in thread %r: %s", agent.name, thread.id, run_output.content
        )
        publish(thread.id, "error", {"agent_id": agent.id, "error": str(run_output.content)})
        message = Message(
            thread_id=thread.id,
            sender_type="system",
            sender_name="system",
            content=f"{agent.name} couldn't respond — its provider returned an error.",
            content_type="system_alert",
        )
        db.add(message)
        return [message]  # provider errors don't count toward guard limits or recurse

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
    await db.flush()  # populate message.id for the agent_message event below
    new_messages: list[Message] = [message]
    publish(
        thread.id,
        "agent_message",
        {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "message_id": message.id,
            "content": content,
            "seq": seq,
        },
    )

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
        publish(
            thread.id,
            "system_alert",
            {
                "type": "guard_paused",
                "reason": guard_state.pause_reason,
                "message": pause_message.content,
            },
        )
        return new_messages

    handoff_call = _find_handoff_call(run_output)
    if handoff_call is not None:
        target_name, handoff_context = handoff_call
        new_messages.extend(
            await _handle_handoff(
                db, thread, channel, guard_state, agent, team_agents, target_name, handoff_context
            )
        )

    # FR-5.6/AC-014: this agent's own message can itself trigger a
    # teammate (e.g. an @mention in its reply) — recurse.
    recursive_messages = await dispatch_and_respond(
        db, thread, channel, content, from_agent_id=agent.id, from_agent_name=agent.name
    )
    new_messages.extend(recursive_messages)
    return new_messages


async def _handle_handoff(
    db: AsyncSession,
    thread: Thread,
    channel: Channel,
    guard_state: ThreadGuardState,
    from_agent: Agent,
    team_agents: list[tuple[Agent, AgentDispatchInfo]],
    target_agent_name: str,
    context: str,
) -> list[Message]:
    """FR-6.1/6.3: post the visible handoff message, then invoke the named
    target directly — bypassing routing rules entirely, the same way an
    @mention does — via the shared _invoke_agent pipeline (FR-6.2: the
    target gets the handoff framed explicitly as its input, plus full
    thread history through the shared AgentOS session, FR-12.2)."""
    target = next(
        (agent for agent, _ in team_agents if agent.name.lower() == target_agent_name.lower()),
        None,
    )
    if target is None:
        logger.warning(
            "Agent %r tried to hand off to unknown agent %r in thread %r",
            from_agent.name,
            target_agent_name,
            thread.id,
        )
        return []

    handoff_message = Message(
        thread_id=thread.id,
        sender_type="system",
        sender_name="system",
        content=f"@{from_agent.name} handed off to @{target.name}: {context}",
        content_type="handoff",
    )
    db.add(handoff_message)
    await db.flush()
    publish(
        thread.id,
        "handoff",
        {"from_agent_id": from_agent.id, "to_agent_name": target.name, "context": context},
    )
    messages: list[Message] = [handoff_message]

    target_messages = await _invoke_agent(
        db,
        thread,
        channel,
        guard_state,
        target,
        f"[Handoff from {from_agent.name}]: {context}",
        team_agents,
        from_agent_id=from_agent.id,
        from_agent_name=from_agent.name,
    )
    messages.extend(target_messages)
    return messages
