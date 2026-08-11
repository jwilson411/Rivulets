"""Bridges the pure DispatchEngine (dispatch/engine.py, DB-free) to our DB
and to AgentOS: loads a channel team's agents + routing rules, runs the
dispatcher, invokes matched agents, and persists their replies as rivulet
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

Publishes SSE events (FR-12.3, streaming.py) as it goes — `agent_status`
before an agent starts and on each tool-call transition (R-9, #30),
`agent_token` per streamed content delta, `agent_message` once a reply is
persisted, `handoff` when one occurs, `error`/`system_alert` on failure or
guard pause, and `done` once per external (non-recursive) call. Persisting rows
and publishing events both happen inline here, in the same request that
triggered the dispatch — see api/rivulets.py's SSE endpoint for how a
concurrent connection observes these live while this coroutine runs.

A message that misses every @mention and deterministic rule falls through
to dispatch/llm_fallback.py's LLM-based fallback (ADR-005 stage 2) before
being dropped as unrouted — see that module's docstring for model
selection and graceful-degradation behavior.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from croniter import CroniterError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos import run_agent
from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.models import AUTO_MODEL, ModelTier, resolve_model, resolve_tier_model
from rivulets.config import get_settings
from rivulets.db.models import (
    Agent,
    AgentPeerPreference,
    AgentRoutingRule,
    Channel,
    DispatchDecision,
    File,
    Message,
    Rivulet,
    RivuletGuardState,
    TeamAgent,
    Workflow,
    WorkflowSchedule,
)
from rivulets.db.session import session_scope
from rivulets.dispatch.approvals import create_or_get_pending_approval
from rivulets.dispatch.budgets import BudgetAlert, budget_alert_text, check_budget_caps
from rivulets.dispatch.complexity_classifier import classify_tier
from rivulets.dispatch.engine import AgentDispatchInfo, DispatchEngine
from rivulets.dispatch.guards import (
    get_or_create_guard_state,
    record_agent_message,
    reset_guard_state,
)
from rivulets.dispatch.llm_fallback import build_llm_fallback
from rivulets.dispatch.rules import Rule, RuleType
from rivulets.streaming import publish
from rivulets.sync import get_sync_engine
from rivulets.sync.agent_dispatch import AgentDispatchRequest
from rivulets.sync.capabilities import load_capabilities
from rivulets.sync.publish import publish_current_state
from rivulets.tracing import TraceContext, finish_span, start_span

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


def _find_run_workflow_call(run_output: RunOutput) -> tuple[str, str] | None:
    """Same shape as _find_handoff_call, for the run_workflow tool
    (tools/builtin/run_workflow.py, #24) — "a human typing '@some-agent
    run this workflow' should let that agent launch it"."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "run_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        name = args.get("workflow_name")
        workflow_input = args.get("workflow_input")
        if isinstance(name, str) and isinstance(workflow_input, str):
            return name, workflow_input
    return None


# #93: the number of WorkflowSchedule rows a single agent can have
# outstanding (WorkflowSchedule.created_by == agent.id) at once, mirroring
# engine.py's own MAX_NODE_VISITS_PER_RUN/MAX_TOTAL_STEPS_PER_RUN
# runaway-execution guards — nothing else stops a misunderstanding, a
# prompt injection, or a loop in an agent's own reasoning from
# proliferating schedules across a long conversation.
MAX_AGENT_SCHEDULES = 20


def _str_or_none(args: dict[str, object], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None


class ScheduleWorkflowCall:
    """Parsed args for a schedule_workflow tool call (#93). Only
    `workflow_name` is required; the rest fall back to None/"" when the
    model didn't pass them (tool_args only reflects what the model
    actually supplied, not the function's own default values)."""

    def __init__(self, args: dict[str, object], workflow_name: str) -> None:
        self.workflow_name = workflow_name
        self.input_content = _str_or_none(args, "input_content") or ""
        self.cron_expression = _str_or_none(args, "cron_expression")
        self.fire_at = _str_or_none(args, "fire_at")
        self.name = _str_or_none(args, "name")


def _find_schedule_workflow_call(run_output: RunOutput) -> ScheduleWorkflowCall | None:
    """Same shape as _find_run_workflow_call, for the schedule_workflow
    tool (tools/builtin/schedules.py, #93)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "schedule_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        workflow_name = args.get("workflow_name")
        if isinstance(workflow_name, str):
            return ScheduleWorkflowCall(args, workflow_name)
    return None


def _find_list_schedules_call(run_output: RunOutput) -> bool:
    """Same shape as _find_handoff_call, for the argument-less
    list_schedules tool (tools/builtin/schedules.py, #93)."""
    return any(tool_call.tool_name == "list_schedules" for tool_call in run_output.tools or [])


def _find_cancel_schedule_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_run_workflow_call, for the cancel_schedule tool
    (tools/builtin/schedules.py, #93)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "cancel_schedule":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        schedule_ref = args.get("schedule")
        if isinstance(schedule_ref, str):
            return schedule_ref
    return None


async def dispatch_and_respond(
    db: AsyncSession,
    rivulet: Rivulet,
    channel: Channel,
    message_content: str,
    *,
    from_agent_id: str | None = None,
    from_agent_name: str | None = None,
    triggering_message_id: str | None = None,
    trace_ctx: TraceContext | None = None,
    attached_files: list[File] | None = None,
) -> list[Message]:
    """Run the dispatcher against `channel`'s team and invoke every matched
    agent, appending its reply to `rivulet` as a Message row. Returns the
    new Message rows (already added to `db`, not yet committed — callers
    own the commit so this composes with whatever else they're persisting
    in the same request).

    `from_agent_id`/`from_agent_name` are set only on recursive calls
    triggered by another agent's own message — omit them for the normal,
    human-triggered path. `triggering_message_id` (issue #10) rides along
    for correlation on a remotely-dispatched agent's request/ack, not used
    locally.

    `attached_files` (#105) is the human message's file attachments, if
    any. It only affects what the invoked agent(s) actually see (a note
    listing each file_id, so an agent can discover and call
    read_attached_file on it) -- it's kept out of the content used for
    dispatch routing and Auto-mode tier classification so an attachment
    list can't skew which agent gets picked or which model tier a message
    classifies into.

    `trace_ctx` (#96) is None on every call site that isn't traced (the
    default) — see tracing.py's module docstring for why tracing is
    opt-in rather than auto-starting here. When set, it's threaded
    unchanged into the DispatchDecision span this call creates and
    everything that span's agent invocations go on to do.
    """
    is_top_level = from_agent_id is None
    try:
        return await _dispatch_and_respond(
            db,
            rivulet,
            channel,
            message_content,
            from_agent_id,
            from_agent_name,
            triggering_message_id,
            trace_ctx,
            attached_files,
        )
    finally:
        # One "no more events for this trigger" signal per external call,
        # regardless of which return path fired above (SSE clients need
        # this even when nothing ended up matching, api-design.md's `done`).
        if is_top_level:
            publish(rivulet.id, "done", {"rivulet_id": rivulet.id})


def _attachment_note(attached_files: list[File] | None) -> str:
    """#105: tells the invoked agent(s) what was attached to the triggering
    message. Without this, an agent has no way to discover a file_id
    exists to pass to read_attached_file -- the message content alone
    doesn't carry it. Deliberately not folded into the dispatch-routing/
    tier-classification content (see dispatch_and_respond's docstring)."""
    if not attached_files:
        return ""
    lines = [
        "",
        "",
        "[Attached files — call read_attached_file(file_id) to access "
        "content; image attachments are returned as visible image content]",
    ]
    lines.extend(
        f"- {f.filename} (file_id={f.id}, {f.mime_type}, {f.size_bytes} bytes)"
        for f in attached_files
    )
    return "\n".join(lines)


async def _dispatch_and_respond(
    db: AsyncSession,
    rivulet: Rivulet,
    channel: Channel,
    message_content: str,
    from_agent_id: str | None,
    from_agent_name: str | None,
    triggering_message_id: str | None = None,
    trace_ctx: TraceContext | None = None,
    attached_files: list[File] | None = None,
) -> list[Message]:
    guard_state = await get_or_create_guard_state(db, rivulet.id)
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
    # R-4 dispatcher hit-rate tracking (#31): one row per routing decision,
    # recursive re-dispatches (FR-5.6) included — each is its own invocation
    # of the same two-stage pipeline and can independently hit the LLM
    # fallback.
    decision = DispatchDecision(method=result.method.value, llm_invoked=result.llm_invoked)
    db.add(decision)
    await db.flush()
    # #96: every agent this decision matches nests under this one span,
    # whether invoked here or (recursively) by one of their own replies.
    dispatch_span_id = await start_span(
        db,
        trace_ctx,
        span_type="dispatch_decision",
        entity_id=decision.id,
        name=f"dispatch ({result.method.value})",
    )
    agent_trace_ctx = (
        TraceContext(trace_ctx.trace_id, dispatch_span_id) if trace_ctx is not None else None
    )

    if rivulet.agentos_session_id is None:
        rivulet.agentos_session_id = rivulet.id  # FR-12.2: one AgentOS session per rivulet

    # #105: routing/tier-classification above used the raw message_content;
    # what the matched agent(s) actually receive gets the attachment note
    # appended, computed once since it's identical for every matched agent
    # this round.
    agent_content = message_content + _attachment_note(attached_files)

    new_messages: list[Message] = []
    for agent_id in result.agent_ids:
        if guard_state.paused:
            # An earlier agent in this same round (or a deeper recursive
            # call sharing this guard_state) just tripped a guard.
            break
        agent = agent_by_id[agent_id]
        remote_peer = await _resolve_remote_peer(db, agent)
        if remote_peer is not None:
            dispatched = await _dispatch_to_remote_peer(
                remote_peer,
                rivulet,
                channel,
                agent,
                agent_content,
                from_agent_id,
                from_agent_name,
                triggering_message_id,
            )
            if dispatched:
                # The reply arrives later via normal Message gossipsub
                # sync, not as a return value here (see agent_dispatch.py's
                # module docstring on why the RPC is ack-only). Untraced:
                # #96 is local-only v1 (see RunTrace's docstring), and the
                # remote peer's own share of this work lands in its own
                # trace history instead.
                continue
            # Ack failed or timed out (peer unreachable, no handler,
            # etc.) -- fall through to running it locally instead, same
            # "offline -> run locally" fallback as _resolve_remote_peer's
            # own not-running/no-match cases.
        new_messages.extend(
            await _invoke_agent(
                db,
                rivulet,
                channel,
                guard_state,
                agent,
                agent_content,
                team_agents,
                from_agent_id=from_agent_id,
                from_agent_name=from_agent_name,
                trace_ctx=agent_trace_ctx,
            )
        )

    await finish_span(db, dispatch_span_id, status="completed")
    return new_messages


async def _resolve_remote_peer(db: AsyncSession, agent: Agent) -> str | None:
    """Issue #10: if `agent` is pinned to a capability tag, and a connected
    peer (other than this node) advertises it, return that peer's id so
    the caller can dispatch there instead of running locally. Returns None
    -- meaning "run locally" -- for every other case: no preference set,
    sync engine not running (confirmed v1 scope: no queue/retry fallback),
    this node already has the preferred capability itself, or no connected
    peer currently advertises it."""
    pref = await db.get(AgentPeerPreference, agent.id)
    if pref is None:
        return None
    engine = get_sync_engine()
    if not engine.running:
        return None
    if pref.capability_tag in load_capabilities(get_settings().sync_dir):
        return None  # this node already matches -- no network hop needed
    for peer_id, tags in (await engine.list_peer_capabilities()).items():
        if pref.capability_tag in tags:
            return peer_id  # first match; no load-balancing in v1
    return None


async def _dispatch_to_remote_peer(
    peer_id: str,
    rivulet: Rivulet,
    channel: Channel,
    agent: Agent,
    message_content: str,
    from_agent_id: str | None,
    from_agent_name: str | None,
    triggering_message_id: str | None,
) -> bool:
    request = AgentDispatchRequest(
        rivulet_id=rivulet.id,
        channel_id=channel.id,
        agent_id=agent.id,
        message_content=message_content,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        triggering_message_id=triggering_message_id,
    )
    return await get_sync_engine().dispatch_agent_remotely(peer_id, request)


async def invoke_agent_remotely(request: AgentDispatchRequest) -> None:
    """Server side of an incoming agent-dispatch RPC (issue #10) -- wired
    to SyncEngine.set_agent_dispatch_handler in app.py. Opens its own DB
    session since it isn't running inside a FastAPI request, the same
    pattern as sync/apply.py's handle_incoming_state_change. Loads
    everything locally, then calls the exact same `_invoke_agent` the
    ordinary in-process dispatch path uses -- no parallel execution
    pipeline. Publishes any resulting messages itself since there's no
    outer request handler to do it (mirrors api/rivulets.py's
    create_rivulet/post_message publishing their own dispatch results)."""
    async with session_scope() as db:
        rivulet = await db.get(Rivulet, request.rivulet_id)
        channel = await db.get(Channel, request.channel_id)
        agent = await db.get(Agent, request.agent_id)
        if rivulet is None or channel is None or agent is None:
            logger.warning(
                "Remote agent dispatch (rivulet=%s channel=%s agent=%s): "
                "entity not found locally yet",
                request.rivulet_id,
                request.channel_id,
                request.agent_id,
            )
            return

        guard_state = await get_or_create_guard_state(db, rivulet.id)
        if guard_state.paused:
            return
        if rivulet.agentos_session_id is None:
            rivulet.agentos_session_id = rivulet.id  # FR-12.2: one AgentOS session per rivulet

        team_agents = (
            await _load_team_dispatch_agents(db, channel.team_id) if channel.team_id else []
        )
        new_messages = await _invoke_agent(
            db,
            rivulet,
            channel,
            guard_state,
            agent,
            request.message_content,
            team_agents,
            from_agent_id=request.from_agent_id,
            from_agent_name=request.from_agent_name,
        )
        await db.commit()
        for message in new_messages:
            await publish_current_state(db, "message", message.id)
        publish(rivulet.id, "done", {"rivulet_id": rivulet.id})


_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_RETRYABLE_KEYWORDS = (
    "timeout",
    "timed out",
    "connection",
    "unreachable",
    "temporarily unavailable",
    "overloaded",
)
_ERROR_CODE_PATTERN = re.compile(r"[Ee]rror code:\s*(\d{3})")


def _is_retryable_error(text: str) -> bool:
    """Classify a failed provider call as worth retrying against a
    fallback model (#103), vs. one any other provider would fail
    identically on. Rate limits (429) and upstream outages (5xx, request
    timeouts) are transient and provider-specific — a different provider
    or model has no reason to hit the same wall. Auth/config errors (401,
    403) and bad-request errors (400, 404, 422) are not: they'd fail the
    same way against the fallback too, so retrying just spends tokens on
    a doomed second attempt.

    agno doesn't raise on a provider HTTP error — it retries internally
    (agent.py's own num_attempts) then gives up and sets `RunOutput.content`
    to `str(exc)` (see test_run_status_error_posts_system_alert_not_raw_
    error_text). Both openai-python and anthropic-python format that as
    "Error code: NNN - ...", which is what's matched here. Failures that
    never reached a provider at all (network errors, our own "not
    registered") carry no status code, so those fall back to a keyword
    match instead.
    """
    match = _ERROR_CODE_PATTERN.search(text)
    if match:
        return int(match.group(1)) in _RETRYABLE_STATUS_CODES
    lowered = text.lower()
    return any(keyword in lowered for keyword in _RETRYABLE_KEYWORDS)


def _fallback_candidates(agent: Agent) -> list[str]:
    """Parse `Agent.fallback_models` (ordered JSON array of
    'provider:model_name' strings) into a plain list. Malformed or empty
    values quietly become no fallback chain rather than an error — a
    corrupt value here shouldn't take an agent offline, same spirit as
    NFR-2.4."""
    if not agent.fallback_models:
        return []
    try:
        raw = json.loads(agent.fallback_models)
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    items = cast(list[object], raw)
    result: list[str] = []
    for item in items:
        if isinstance(item, str) and item and item != AUTO_MODEL:
            result.append(item)
    return result


async def _run_agent_with_fallback(
    db: AsyncSession,
    session_id: str,
    agent: Agent,
    message_content: str,
    on_token: Callable[[str], None],
    on_status: Callable[[str, str | None], None],
    *,
    model_used: str | None,
) -> tuple[RunOutput | None, Exception | None, str, bool]:
    """Try `agent`'s primary model, then each entry in its configured
    fallback chain in order (#103), stopping at the first success or the
    first non-retryable failure. Returns
    (run_output, exception, served_model, fallback_used) — exactly one of
    run_output/exception is set on return; served_model is always the
    last model attempted.
    """
    primary_model = model_used or agent.model
    chain = [primary_model, *_fallback_candidates(agent)]
    seen: set[str] = set()
    ordered = [c for c in chain if c and not (c in seen or seen.add(c))]

    run_output: RunOutput | None = None
    last_exc: Exception | None = None
    served_model = ordered[0]

    for i, candidate in enumerate(ordered):
        is_last = i == len(ordered) - 1
        served_model = candidate
        try:
            # The primary attempt of a non-auto agent reuses AgentOS's
            # already-registered model exactly as before fallback chains
            # existed (no extra resolve/keychain round-trip on the common
            # no-fallback path) — only auto mode and fallback attempts
            # need a per-call override.
            model_override = (
                None if (i == 0 and model_used is None) else await resolve_model(db, candidate)
            )
            run_output = await run_agent(
                db,
                agent.id,
                message_content,
                session_id=session_id,
                user_id="human",
                on_token=on_token,
                on_status=on_status,
                model_override=model_override,
            )
        except Exception as exc:
            last_exc = exc
            run_output = None
            if not is_last and _is_retryable_error(str(exc)):
                logger.info(
                    "Agent %r: model %r failed (%s); trying fallback %r",
                    agent.name,
                    candidate,
                    exc,
                    ordered[i + 1],
                )
                continue
            break

        last_exc = None
        if (
            run_output.status is RunStatus.error
            and not is_last
            and _is_retryable_error(str(run_output.content))
        ):
            logger.info(
                "Agent %r: model %r returned a retryable error; trying fallback %r",
                agent.name,
                candidate,
                ordered[i + 1],
            )
            continue
        break

    return run_output, last_exc, served_model, served_model != ordered[0]


def _post_budget_alert(
    db: AsyncSession, rivulet_id: str, alert: BudgetAlert, *, blocked: bool
) -> Message:
    """#97: persist a system_alert Message and publish the matching SSE
    event for a breached budget cap, mirroring guards.py's _pause Message
    shape and this module's own "guard_paused" SSE event shape."""
    text = budget_alert_text(alert, blocked=blocked)
    message = Message(
        rivulet_id=rivulet_id,
        sender_type="system",
        sender_name="system",
        content=text,
        content_type="system_alert",
    )
    db.add(message)
    publish(
        rivulet_id,
        "system_alert",
        {
            "type": "budget_exceeded",
            "cap_id": alert.cap.id,
            "scope_type": alert.cap.scope_type,
            "scope_id": alert.cap.agent_id or alert.cap.team_id,
            "period": alert.cap.period,
            "action": alert.cap.action,
            "limit_usd": alert.cap.limit_usd,
            "spend_usd": alert.status.spend_usd,
            "unpriced_run_count": alert.status.unpriced_run_count,
            "blocked": blocked,
            "message": text,
        },
    )
    return message


async def _invoke_agent(
    db: AsyncSession,
    rivulet: Rivulet,
    channel: Channel,
    guard_state: RivuletGuardState,
    agent: Agent,
    message_content: str,
    team_agents: list[tuple[Agent, AgentDispatchInfo]],
    *,
    from_agent_id: str | None,
    from_agent_name: str | None,
    trace_ctx: TraceContext | None = None,
) -> list[Message]:
    """Run one agent, persist its reply (or a failure notice), update
    guard state, then act on whatever the run implies: a handoff call
    (FR-6), a tripped guard (stop), or neither (recurse — FR-5.6). Shared
    by the main dispatch loop and _handle_handoff's target invocation,
    since both need the identical run/error/persist/guard/recurse pipeline.
    """
    new_messages: list[Message] = []
    budget_alerts, budget_blocking = await check_budget_caps(db, agent, channel.team_id)
    for budget_alert in budget_alerts:
        new_messages.append(_post_budget_alert(db, rivulet.id, budget_alert, blocked=False))
    if budget_blocking is not None:
        # #97: refuses this invocation only -- does NOT set guard_state.paused
        # (that's rivulet-wide conversational pause semantics; a budget block
        # is scope-specific and shouldn't silently freeze an unrelated agent
        # in the same rivulet), does NOT recurse or call the model, and --
        # like a guard-paused call, which never reaches _invoke_agent at all
        # -- never opens a #96 trace span below: there's no run to trace.
        new_messages.append(_post_budget_alert(db, rivulet.id, budget_blocking, blocked=True))
        # #102: surface the block as an actionable inbox item, not just a
        # message the human has to remember to act on -- dedup'd against
        # this cap, so a rivulet that keeps re-triggering the same blocked
        # agent doesn't grow a new row every turn.
        await create_or_get_pending_approval(
            db,
            "budget",
            budget_cap_id=budget_blocking.cap.id,
            title=f"Budget cap exceeded for {budget_blocking.cap.scope_type}",
            detail=budget_alert_text(budget_blocking, blocked=True),
        )
        await db.flush()
        return new_messages

    # #96: opened before the run so its duration covers the actual LLM
    # call, not just the accounting that happens after it returns --
    # entity_id is back-filled once record_agent_run creates the AgentRun
    # row below (that row doesn't exist yet at span-open time).
    agent_span_id = await start_span(
        db, trace_ctx, span_type="agent_run", entity_id=None, name=agent.name
    )
    child_trace_ctx = (
        TraceContext(trace_ctx.trace_id, agent_span_id) if trace_ctx is not None else None
    )
    seq = 0

    def on_token(delta: str, agent_id: str = agent.id, agent_name: str = agent.name) -> None:
        nonlocal seq
        seq += 1
        publish(
            rivulet.id,
            "agent_token",
            {"agent_id": agent_id, "agent_name": agent_name, "token": delta, "seq": seq},
        )

    def on_status(
        status: str, detail: str | None, agent_id: str = agent.id, agent_name: str = agent.name
    ) -> None:
        publish(
            rivulet.id,
            "agent_status",
            {"agent_id": agent_id, "agent_name": agent_name, "status": status, "detail": detail},
        )

    # R-9's "agent status indicators": fires before run_agent even starts,
    # covering the gap between invocation and either a tool call or the
    # first streamed token — agno's own RunStartedEvent fires no earlier
    # than this point would anyway, so there's nothing to wait on.
    on_status("thinking", None)

    model_used: str | None = None
    model_tier: ModelTier | None = None
    if agent.model == AUTO_MODEL:
        # Auto mode (#23): classify this message's complexity, resolve the
        # matching tier to a concrete model, fresh on every call. If no
        # tier model can be resolved (e.g. no provider configured),
        # model_used stays None and run_agent falls through to the agent's
        # already-registered (cheap-tier) model -- see agentos/service.py's
        # _build_agno_agent "auto" fallback for the other half of this.
        model_tier = await classify_tier(db, message_content)
        model_used = await resolve_tier_model(db, model_tier)

    assert rivulet.agentos_session_id is not None  # set by the top-level call before any agent runs
    run_output, exc, served_model, fallback_used = await _run_agent_with_fallback(
        db,
        rivulet.agentos_session_id,
        agent,
        message_content,
        on_token,
        on_status,
        model_used=model_used,
    )
    # The model that was actually asked for, before any fallback -- kept
    # for accounting (AgentRun.requested_model) below. Only differs from
    # served_model when fallback_used.
    requested_model = model_used or agent.model

    if run_output is None:
        # NFR-2.4: one agent's provider being unreachable doesn't stop
        # others in the same dispatch from responding. Covers failures in
        # our own run_agent() (e.g. "not registered") that happen before
        # agno even gets a chance to run, and the case where every entry
        # in the fallback chain (#103) was exhausted without success.
        assert exc is not None
        logger.warning(
            "Agent %r failed to respond in rivulet %r", agent.name, rivulet.id, exc_info=exc
        )
        publish(rivulet.id, "error", {"agent_id": agent.id, "error": str(exc)})
        await finish_span(db, agent_span_id, status="error")
        return new_messages

    if run_output.status is RunStatus.error:
        # Observed in practice: a bad API key doesn't raise — agno catches
        # the provider's HTTP error and returns a normal-looking RunOutput
        # whose `content` is the raw error string. Surfacing that as if
        # the agent said it would be confusing (NFR-5.4: plain-language
        # errors, not raw exception text) and wrong — it's not something
        # the agent "said".
        logger.warning(
            "Agent %r run failed in rivulet %r: %s", agent.name, rivulet.id, run_output.content
        )
        publish(rivulet.id, "error", {"agent_id": agent.id, "error": str(run_output.content)})
        error_run = await record_agent_run(
            db,
            agent,
            served_model,
            model_tier,
            "error",
            run_output,
            requested_model=requested_model if fallback_used else None,
        )
        await finish_span(
            db,
            agent_span_id,
            status="error",
            entity_id=error_run.id,
            model=served_model,
            cost_usd=error_run.cost_usd,
            total_tokens=error_run.total_tokens,
        )
        message = Message(
            rivulet_id=rivulet.id,
            sender_type="system",
            sender_name="system",
            content=f"{agent.name} couldn't respond — its provider returned an error.",
            content_type="system_alert",
        )
        db.add(message)
        new_messages.append(message)
        return new_messages  # provider errors don't count toward guard limits or recurse

    # get_content_as_string()'s **kwargs is Unknown in agno's own stubs.
    content = run_output.get_content_as_string() or ""  # pyright: ignore[reportUnknownMemberType]
    # Auto mode (#23) visibility (model_used/tier) and issue #10's "where
    # did this run execute" (executed_node_id) both ride the same
    # previously-unused, already-synced metadata_json column -- no schema/
    # sync work needed for either. executed_node_id is always attached
    # (None when the sync engine isn't running), unlike model_used/tier
    # which stay None for non-auto agents. served_model (#103) is the same
    # idiom: only set when a fallback actually served this reply, so a
    # normal run's metadata looks exactly like it did before #103.
    engine = get_sync_engine()
    message_metadata = {
        "model_used": model_used,
        "tier": model_tier,
        "executed_node_id": engine.node_id if engine.running else None,
        "served_model": served_model if fallback_used else None,
    }
    message = Message(
        rivulet_id=rivulet.id,
        sender_type="agent",
        sender_id=agent.id,
        sender_name=agent.name,
        content=content,
        metadata_json=json.dumps(message_metadata),
    )
    db.add(message)
    completed_run = await record_agent_run(
        db,
        agent,
        served_model,
        model_tier,
        "completed",
        run_output,
        requested_model=requested_model if fallback_used else None,
    )
    await finish_span(
        db,
        agent_span_id,
        status="completed",
        entity_id=completed_run.id,
        model=served_model,
        cost_usd=completed_run.cost_usd,
        total_tokens=completed_run.total_tokens,
    )
    await db.flush()  # populate message.id for the agent_message event below
    new_messages.append(message)
    publish(
        rivulet.id,
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
        rivulet.id,
        guard_state,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name or "",
        to_agent_id=agent.id,
        to_agent_name=agent.name,
    )
    if pause_message is not None:
        rivulet.status = "paused"
        db.add(pause_message)
        new_messages.append(pause_message)
        publish(
            rivulet.id,
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
                db,
                rivulet,
                channel,
                guard_state,
                agent,
                team_agents,
                target_name,
                handoff_context,
                trace_ctx=child_trace_ctx,
            )
        )

    run_workflow_call = _find_run_workflow_call(run_output)
    if run_workflow_call is not None:
        workflow_name, workflow_input = run_workflow_call
        await _handle_run_workflow_trigger(
            db, rivulet, agent, workflow_name, workflow_input, trace_ctx=child_trace_ctx
        )

    schedule_workflow_call = _find_schedule_workflow_call(run_output)
    if schedule_workflow_call is not None:
        new_messages.extend(
            await _handle_schedule_workflow_trigger(db, rivulet, agent, schedule_workflow_call)
        )

    if _find_list_schedules_call(run_output):
        new_messages.extend(await _handle_list_schedules_trigger(db, rivulet, agent))

    cancel_schedule_call = _find_cancel_schedule_call(run_output)
    if cancel_schedule_call is not None:
        new_messages.extend(
            await _handle_cancel_schedule_trigger(db, rivulet, agent, cancel_schedule_call)
        )

    # FR-5.6/AC-014: this agent's own message can itself trigger a
    # teammate (e.g. an @mention in its reply) — recurse.
    recursive_messages = await dispatch_and_respond(
        db,
        rivulet,
        channel,
        content,
        from_agent_id=agent.id,
        from_agent_name=agent.name,
        trace_ctx=child_trace_ctx,
    )
    new_messages.extend(recursive_messages)
    return new_messages


async def _handle_handoff(
    db: AsyncSession,
    rivulet: Rivulet,
    channel: Channel,
    guard_state: RivuletGuardState,
    from_agent: Agent,
    team_agents: list[tuple[Agent, AgentDispatchInfo]],
    target_agent_name: str,
    context: str,
    *,
    trace_ctx: TraceContext | None = None,
) -> list[Message]:
    """FR-6.1/6.3: post the visible handoff message, then invoke the named
    target directly — bypassing routing rules entirely, the same way an
    @mention does — via the shared _invoke_agent pipeline (FR-6.2: the
    target gets the handoff framed explicitly as its input, plus full
    rivulet history through the shared AgentOS session, FR-12.2)."""
    target = next(
        (agent for agent, _ in team_agents if agent.name.lower() == target_agent_name.lower()),
        None,
    )
    if target is None:
        logger.warning(
            "Agent %r tried to hand off to unknown agent %r in rivulet %r",
            from_agent.name,
            target_agent_name,
            rivulet.id,
        )
        return []

    handoff_message = Message(
        rivulet_id=rivulet.id,
        sender_type="system",
        sender_name="system",
        content=f"@{from_agent.name} handed off to @{target.name}: {context}",
        content_type="handoff",
    )
    db.add(handoff_message)
    await db.flush()
    publish(
        rivulet.id,
        "handoff",
        {"from_agent_id": from_agent.id, "to_agent_name": target.name, "context": context},
    )
    messages: list[Message] = [handoff_message]

    target_messages = await _invoke_agent(
        db,
        rivulet,
        channel,
        guard_state,
        target,
        f"[Handoff from {from_agent.name}]: {context}",
        team_agents,
        from_agent_id=from_agent.id,
        from_agent_name=from_agent.name,
        trace_ctx=trace_ctx,
    )
    messages.extend(target_messages)
    return messages


async def _handle_run_workflow_trigger(
    db: AsyncSession,
    rivulet: Rivulet,
    agent: Agent,
    workflow_name: str,
    workflow_input: str,
    *,
    trace_ctx: TraceContext | None = None,
) -> None:
    """#24: an agent called the run_workflow tool — look up the named
    workflow and actually execute it (workflows/engine.py). Imports
    rivulets.workflows lazily: that package's node executors
    (workflows/nodes.py) import dispatch/rule_generation.py for their
    "summarize" node's model selection, so a module-level import here
    would risk a circular import depending on which package happens to
    get imported first at app startup — deferring it to call time (well
    after both packages are fully loaded) sidesteps that entirely.
    Doesn't return anything for the caller to persist: run_workflow is
    self-contained the same way invoke_agent_remotely is, committing and
    publishing each message it produces as it goes (see engine.py's
    _post_message)."""
    from rivulets.workflows import find_workflow_by_name, run_workflow

    workflow = await find_workflow_by_name(db, workflow_name)
    if workflow is None:
        logger.warning(
            "Agent %r tried to run unknown workflow %r in rivulet %r",
            agent.name,
            workflow_name,
            rivulet.id,
        )
        return
    await run_workflow(
        db,
        workflow,
        rivulet,
        workflow_input,
        triggered_by="agent",
        triggered_by_id=agent.id,
        trace_ctx=trace_ctx,
    )


def _normalize_fire_at(fire_at: str) -> str:
    """Parses an ISO 8601 timestamp (the schedule_workflow tool's
    `fire_at` arg) into the same "%Y-%m-%dT%H:%M:%SZ" UTC shape
    workflows/scheduler.py's compute_next_fire_at produces, so
    WorkflowSchedule.next_fire_at is one consistent format regardless of
    which path created the row. Raises ValueError on anything
    unparseable — the same exception type _handle_schedule_workflow_trigger
    already catches for an invalid cron_expression."""
    parsed = datetime.fromisoformat(fire_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _agent_schedules(db: AsyncSession, agent_id: str) -> list[WorkflowSchedule]:
    """Every WorkflowSchedule this agent created (#93) — the ownership
    boundary _handle_list_schedules_trigger and _handle_cancel_schedule_trigger
    both enforce, so an agent can only ever see or cancel its own
    schedules, never another agent's or a human's."""
    return list(
        (
            await db.scalars(
                select(WorkflowSchedule)
                .where(WorkflowSchedule.created_by == agent_id)
                .order_by(WorkflowSchedule.created_at)
            )
        ).all()
    )


def _system_message(db: AsyncSession, rivulet: Rivulet, content: str) -> Message:
    """Builds a system_alert Message and stages it on `db` -- every one of
    #93's schedule_workflow/list_schedules/cancel_schedule outcomes
    (success and every rejection) needs one, so staging happens here
    rather than at each call site, where forgetting it would silently
    drop the message from the caller's returned list without erroring."""
    message = Message(
        rivulet_id=rivulet.id,
        sender_type="system",
        sender_name="system",
        content=content,
        content_type="system_alert",
    )
    db.add(message)
    return message


async def _handle_schedule_workflow_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: ScheduleWorkflowCall
) -> list[Message]:
    """#93: an agent called the schedule_workflow tool — validate the
    request and, if well-formed, create a WorkflowSchedule (#92). Unlike
    run_workflow (silent on a bad request, since it has nothing useful to
    say), this always posts a visible confirmation or rejection: a
    schedule is a standing background effect the human in this
    conversation needs to actually see, not just take the agent's own
    unconfirmed word for. Imports rivulets.workflows lazily for the same
    circular-import reason as _handle_run_workflow_trigger."""
    from rivulets.workflows import find_workflow_by_name
    from rivulets.workflows.scheduler import compute_next_fire_at

    workflow = await find_workflow_by_name(db, call.workflow_name)
    if workflow is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to schedule a workflow, but no published workflow "
                f"named {call.workflow_name!r} exists.",
            )
        ]

    if bool(call.cron_expression) == bool(call.fire_at):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to schedule workflow /{workflow.name}, but the request "
                "must specify exactly one of a recurring cron schedule or a one-time fire time.",
            )
        ]

    # A one-off that already fired is inert (scheduler.py's _fire disables
    # it and never re-arms it) -- it doesn't count against the cap, or an
    # agent that mostly sends one-time reminders would eventually exhaust
    # its quota on schedules that aren't outstanding in any sense.
    existing = [
        s for s in await _agent_schedules(db, agent.id) if not (s.run_once and s.last_fired_at)
    ]
    if len(existing) >= MAX_AGENT_SCHEDULES:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to schedule workflow /{workflow.name}, but it already has "
                f"{len(existing)} scheduled workflows outstanding (limit {MAX_AGENT_SCHEDULES}) "
                "— cancel one first.",
            )
        ]

    run_once = call.fire_at is not None
    try:
        if run_once:
            assert call.fire_at is not None
            next_fire_at = _normalize_fire_at(call.fire_at)
        else:
            assert call.cron_expression is not None
            next_fire_at = compute_next_fire_at(call.cron_expression)
    except (CroniterError, ValueError) as exc:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to schedule workflow /{workflow.name}, but the "
                f"schedule was invalid: {exc}",
            )
        ]

    schedule = WorkflowSchedule(
        workflow_id=workflow.id,
        channel_id=rivulet.channel_id,
        cron_expression=call.cron_expression,
        run_once=run_once,
        input_content=call.input_content,
        # #93: agent-created schedules need human approval before they can
        # fire — the same "unilateral agent action doesn't take effect
        # without a human" precedent #84 already established for
        # draft/published workflows.
        enabled=False,
        next_fire_at=next_fire_at,
        name=call.name,
        created_by=agent.id,
    )
    db.add(schedule)
    await db.flush()

    when = f"once at {next_fire_at}" if run_once else f"on schedule {call.cron_expression!r}"
    await create_or_get_pending_approval(
        db,
        "schedule",
        schedule_id=schedule.id,
        title=f"@{agent.name} wants to schedule /{workflow.name}",
        detail=f"Runs {when}. Input: {call.input_content!r}",
    )
    message = _system_message(
        db,
        rivulet,
        f"@{agent.name} scheduled workflow /{workflow.name} to run {when} "
        f"(id: {schedule.id}) — pending human approval before it starts firing.",
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "schedule_created", "schedule_id": schedule.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_list_schedules_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#93: an agent called the list_schedules tool, scoped strictly to
    WorkflowSchedule.created_by == agent.id (see _agent_schedules)."""
    schedules = await _agent_schedules(db, agent.id)
    if not schedules:
        content = f"@{agent.name} has no scheduled workflows."
    else:
        workflow_ids = {s.workflow_id for s in schedules}
        workflows = (await db.scalars(select(Workflow).where(Workflow.id.in_(workflow_ids)))).all()
        workflow_names = {w.id: w.name for w in workflows}
        lines = [f"@{agent.name}'s scheduled workflows:"]
        for s in schedules:
            when = (
                f"once at {s.next_fire_at}"
                if s.run_once
                else f"cron {s.cron_expression!r}, next at {s.next_fire_at}"
            )
            status = (
                "enabled"
                if s.enabled
                else "pending approval"
                if s.last_fired_at is None
                else "disabled"
            )
            label = f" ({s.name})" if s.name else ""
            workflow_name = workflow_names.get(s.workflow_id, s.workflow_id)
            lines.append(f"- /{workflow_name}{label}: {when} [{status}] (id: {s.id})")
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


async def _handle_cancel_schedule_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, schedule_ref: str
) -> list[Message]:
    """#93: an agent called the cancel_schedule tool. `schedule_ref` may
    be a schedule id or its `name` label; either way, ownership is
    enforced by only ever matching against _agent_schedules(agent.id) —
    an agent can cancel a schedule it created, never one it merely knows
    the id of. An id match is checked first and is unambiguous by
    construction (it's the primary key); `name` isn't unique -- nothing
    stops schedule_workflow from creating two schedules with the same
    name -- so a name match that isn't exactly one row is treated as
    ambiguous rather than silently deleting whichever one sorts first."""
    schedules = await _agent_schedules(db, agent.id)
    by_id = next((s for s in schedules if s.id == schedule_ref), None)

    if by_id is not None:
        await db.delete(by_id)
        content = f"@{agent.name} cancelled schedule {schedule_ref!r} (id: {by_id.id})."
    else:
        name_matches = [s for s in schedules if s.name == schedule_ref]
        if len(name_matches) == 1:
            match = name_matches[0]
            await db.delete(match)
            content = f"@{agent.name} cancelled schedule {schedule_ref!r} (id: {match.id})."
        elif name_matches:
            ids = ", ".join(s.id for s in name_matches)
            content = (
                f"@{agent.name} tried to cancel schedule {schedule_ref!r}, but that name matches "
                f"{len(name_matches)} schedules ({ids}) -- cancel by id instead."
            )
        else:
            content = (
                f"@{agent.name} tried to cancel schedule {schedule_ref!r}, but no schedule with "
                "that id or name was found among the ones it created."
            )

    message = _system_message(db, rivulet, content)
    return [message]
