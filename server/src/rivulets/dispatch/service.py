"""Bridges the pure DispatchEngine (dispatch/engine.py, DB-free) to our DB
and to AgentOS: loads a channel team's agents + routing rules, runs the
dispatcher, invokes matched agents, and persists their replies as rivulet
messages (FR-4.1, FR-5.2, FR-12.1).

Also recurses: an agent's own reply is itself re-dispatched (FR-5.6,
AC-014's "Architect mentions @DBA, DBA responds" scenario). The speaker
is excluded from unsolicited re-matching — an `always` agent answering a
human must not then answer its own "how can I help?" — but an explicit
@mention or a specialist keyword on a *teammate* still fires. Mention
and handoff ping-pong is what loop-prevention guards (FR-7,
dispatch/guards.py) bound; without recursion those loops are
structurally impossible. Recursion depth is bounded by the guard checks
running before each invocation, not by a separate depth counter: worst
case is ~guard.turn_limit calls deep, comfortably under Python's
recursion limit for the FR-7.4-documented range (1-100).

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
and publishing events both happen inline here while this coroutine
runs — api/rivulets.py kicks this off as a BackgroundTask after the
human-message POST returns (#413) so an SSE subscriber can observe the
round without waiting on that HTTP response.

A message that misses every @mention and deterministic rule falls through
to dispatch/llm_fallback.py's LLM-based fallback (ADR-005 stage 2) before
being dropped as unrouted — see that module's docstring for model
selection and graceful-degradation behavior.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from croniter import CroniterError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos import run_agent, sync_agents
from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.agent_lifecycle import (
    generate_and_store_routing_rules,
    publish_agent_change,
    publish_agent_teams_change,
    publish_agent_tools_change,
    record_agent_version,
    register_agent_with_agentos,
    replace_routing_rules,
    set_agent_teams,
    set_agent_tools,
)
from rivulets.agentos.mcp import (
    MCPConnectionError,
    discover_tools,
    get_server_env,
    get_server_headers,
    mcp_env_ref,
    mcp_header_ref,
)
from rivulets.agentos.models import AUTO_MODEL, ModelTier, resolve_model, resolve_tier_model
from rivulets.agentos.tool_resolution import is_builtin_tool_authorized
from rivulets.api.agents import agent_holds_owner_scope, find_unauthorized_tool_assignment
from rivulets.api.teams import team_holds_owner_scoped_agent
from rivulets.config import get_settings
from rivulets.db.base import utcnow_iso
from rivulets.db.models import (
    Agent,
    AgentPeerPreference,
    AgentRoutingRule,
    AgentVersion,
    Channel,
    DispatchDecision,
    File,
    Invite,
    MCPServer,
    Message,
    Rivulet,
    RivuletGuardState,
    Team,
    TeamAgent,
    Tool,
    Workflow,
    WorkflowConnection,
    WorkflowSchedule,
    WorkspaceSetting,
)
from rivulets.db.session import begin_immediate, session_scope
from rivulets.dispatch.approvals import create_or_get_pending_approval
from rivulets.dispatch.budgets import BudgetAlert, budget_alert_text, check_budget_caps
from rivulets.dispatch.complexity_classifier import classify_tier
from rivulets.dispatch.engine import (
    AgentDispatchInfo,
    DispatchEngine,
    DispatchMethod,
    DispatchResult,
)
from rivulets.dispatch.guards import (
    get_or_create_guard_state,
    record_agent_message,
    reset_guard_state,
)
from rivulets.dispatch.llm_fallback import build_llm_fallback
from rivulets.dispatch.rules import Rule, RuleType, is_valid_regex
from rivulets.security import keys
from rivulets.security.credentials import delete_secret
from rivulets.security.network import BlockedHostError, check_host_is_public, detect_lan_address
from rivulets.streaming import publish
from rivulets.sync import get_sync_engine
from rivulets.sync.agent_dispatch import AgentDispatchRequest
from rivulets.sync.apply import TEAM_AGENT_SPEC
from rivulets.sync.capabilities import load_capabilities
from rivulets.sync.publish import (
    publish_current_state,
    publish_tombstone,
    replace_join_entities,
)
from rivulets.tracing import TraceContext, finish_span, start_span

logger = logging.getLogger(__name__)

_ARRAY_PATTERN_TYPES = {RuleType.KEYWORD, RuleType.SEMANTIC}


async def _authorize_builtin_call(db: AsyncSession, agent: Agent, tool_name: str) -> bool:
    """#240: gates every scoped builtin trigger handler below on
    is_builtin_tool_authorized, re-checked fresh against this run's
    `agent` right before acting on it -- the `_find_*_call` helpers above
    only look at `tool_call.tool_name`, a plain string on the completed
    run, which says nothing about whether the thing that actually ran was
    the real scoped builtin (as opposed to a same-named custom/MCP tool
    that needed no scope at all) or whether the scope grant behind it is
    still current. A denial here is treated the same as "this run made no
    such tool call" -- logged and silently skipped, not surfaced to the
    rivulet -- since the legitimate trigger for it (a real, authorized
    call) never happened."""
    if await is_builtin_tool_authorized(db, agent, tool_name):
        return True
    logger.warning(
        "Agent %r's run included a %r tool call that isn't backed by an authorized "
        "builtin (missing/revoked scope grant, or a same-named custom/MCP tool) — "
        "refusing to act on it",
        agent.name,
        tool_name,
    )
    return False


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


class CreateChannelCall:
    """Parsed args for a create_channel tool call (#189)."""

    def __init__(self, name: str, args: dict[str, object]) -> None:
        self.name = name
        self.description = _str_or_none(args, "description")


class UpdateChannelCall:
    """Parsed args for an update_channel tool call (#189). Only `channel`
    (the id-or-name reference) is required; `name`/`description` fall
    back to None when the model didn't supply them, same as
    ScheduleWorkflowCall above."""

    def __init__(self, channel_ref: str, args: dict[str, object]) -> None:
        self.channel_ref = channel_ref
        self.name = _str_or_none(args, "name")
        self.description = _str_or_none(args, "description")


def _find_create_channel_call(run_output: RunOutput) -> CreateChannelCall | None:
    """Same shape as _find_run_workflow_call, for the create_channel tool
    (tools/builtin/channels.py, #189)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "create_channel":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        name = args.get("name")
        if isinstance(name, str):
            return CreateChannelCall(name, args)
    return None


def _find_update_channel_call(run_output: RunOutput) -> UpdateChannelCall | None:
    """Same shape as _find_run_workflow_call, for the update_channel tool
    (tools/builtin/channels.py, #189)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_channel":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        channel_ref = args.get("channel")
        if isinstance(channel_ref, str):
            return UpdateChannelCall(channel_ref, args)
    return None


def _find_archive_channel_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the archive_channel
    tool (tools/builtin/channels.py, #189)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "archive_channel":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        channel_ref = args.get("channel")
        if isinstance(channel_ref, str):
            return channel_ref
    return None


def _find_unarchive_channel_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the unarchive_channel
    tool (tools/builtin/channels.py, #189)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "unarchive_channel":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        channel_ref = args.get("channel")
        if isinstance(channel_ref, str):
            return channel_ref
    return None


def _find_reorder_channels_call(run_output: RunOutput) -> list[str] | None:
    """Same shape as _find_run_workflow_call, for the reorder_channels
    tool (tools/builtin/channels.py, #189)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "reorder_channels":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        order = args.get("order")
        if isinstance(order, list):
            items = cast(list[Any], order)
            if all(isinstance(item, str) for item in items):
                return cast(list[str], order)
    return None


def _find_list_channels_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_schedules_call, for the argument-less
    list_channels tool (tools/builtin/channels.py, #189)."""
    return any(tool_call.tool_name == "list_channels" for tool_call in run_output.tools or [])


def _str_list(args: dict[str, object], key: str) -> list[str] | None:
    """Returns `args[key]` if it's a list of strings, else None -- used
    (#190) wherever a tool call's optional list arg needs to preserve the
    same "missing means don't touch, [] means clear" distinction
    AgentUpdate/TeamUpdate make at the HTTP layer: args.get(key) already
    returns None for a key the model didn't supply, and this only
    overrides that to None too if the value is present but malformed
    (not a list, or a list with a non-string item), never turning an
    explicit [] into anything else."""
    value = args.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in cast(list[Any], value)):
        return cast(list[str], value)
    return None


class CreateAgentCall:
    """Parsed args for a create_agent tool call (#190)."""

    def __init__(self, name: str, args: dict[str, object]) -> None:
        self.name = name
        self.description = _str_or_none(args, "description") or ""
        self.instructions = _str_or_none(args, "instructions") or ""
        self.model = _str_or_none(args, "model") or ""
        self.team_ids = _str_list(args, "team_ids") or []


def _find_create_agent_call(run_output: RunOutput) -> CreateAgentCall | None:
    """Same shape as _find_run_workflow_call, for the create_agent tool
    (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "create_agent":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        name = args.get("name")
        if isinstance(name, str):
            return CreateAgentCall(name, args)
    return None


class UpdateAgentCall:
    """Parsed args for an update_agent tool call (#190). Only `agent`
    (the id-or-name reference) is required; every other field falls back
    to None ("don't touch") when the model didn't supply it -- see
    _str_list's docstring for why tool_ids/team_ids specifically preserve
    the None-vs-[] distinction rather than defaulting to []."""

    def __init__(self, agent_ref: str, args: dict[str, object]) -> None:
        self.agent_ref = agent_ref
        self.name = _str_or_none(args, "name")
        self.description = _str_or_none(args, "description")
        self.instructions = _str_or_none(args, "instructions")
        self.model = _str_or_none(args, "model")
        self.tool_ids = _str_list(args, "tool_ids")
        self.team_ids = _str_list(args, "team_ids")


def _find_update_agent_call(run_output: RunOutput) -> UpdateAgentCall | None:
    """Same shape as _find_run_workflow_call, for the update_agent tool
    (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_agent":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        agent_ref = args.get("agent")
        if isinstance(agent_ref, str):
            return UpdateAgentCall(agent_ref, args)
    return None


def _find_delete_agent_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the delete_agent tool
    (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "delete_agent":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        agent_ref = args.get("agent")
        if isinstance(agent_ref, str):
            return agent_ref
    return None


class UpdateAgentRoutingRulesCall:
    """Parsed args for an update_agent_routing_rules tool call (#190)."""

    def __init__(self, agent_ref: str, rules: list[dict[str, object]]) -> None:
        self.agent_ref = agent_ref
        self.rules = rules


def _find_update_agent_routing_rules_call(
    run_output: RunOutput,
) -> UpdateAgentRoutingRulesCall | None:
    """Same shape as _find_run_workflow_call, for the
    update_agent_routing_rules tool (tools/builtin/agents_teams.py,
    #190). `rules` must be a list of dicts to even be considered a call
    worth handling -- each dict's own fields (rule_type/pattern/priority)
    are validated later, in _handle_update_agent_routing_rules_trigger,
    the same "parse loosely here, validate meaningfully in the handler"
    split every other parser in this module follows."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_agent_routing_rules":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        agent_ref = args.get("agent")
        rules = args.get("rules")
        if isinstance(agent_ref, str) and isinstance(rules, list):
            items = cast(list[Any], rules)
            if all(isinstance(item, dict) for item in items):
                return UpdateAgentRoutingRulesCall(agent_ref, cast(list[dict[str, object]], rules))
    return None


class UpdateAgentPeerPreferenceCall:
    """Parsed args for an update_agent_peer_preference tool call (#190).
    `capability_tag` is None both when the model omitted it and when it
    explicitly passed None -- either way means "clear", the same
    contract PeerPreferenceIn's field default gives the HTTP endpoint."""

    def __init__(self, agent_ref: str, capability_tag: str | None) -> None:
        self.agent_ref = agent_ref
        self.capability_tag = capability_tag


def _find_update_agent_peer_preference_call(
    run_output: RunOutput,
) -> UpdateAgentPeerPreferenceCall | None:
    """Same shape as _find_run_workflow_call, for the
    update_agent_peer_preference tool (tools/builtin/agents_teams.py,
    #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_agent_peer_preference":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        agent_ref = args.get("agent")
        if isinstance(agent_ref, str):
            return UpdateAgentPeerPreferenceCall(agent_ref, _str_or_none(args, "capability_tag"))
    return None


class RollbackAgentVersionCall:
    """Parsed args for a rollback_agent_version tool call (#190)."""

    def __init__(self, agent_ref: str, version: int) -> None:
        self.agent_ref = agent_ref
        self.version = version


def _find_rollback_agent_version_call(run_output: RunOutput) -> RollbackAgentVersionCall | None:
    """Same shape as _find_run_workflow_call, for the
    rollback_agent_version tool (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "rollback_agent_version":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        agent_ref = args.get("agent")
        version = args.get("version")
        # bool is an int subclass -- excluded explicitly so a model
        # accidentally passing a bare `true`/`false` doesn't get
        # coerced into version 1/0.
        version_ok = isinstance(version, int) and not isinstance(version, bool)
        if isinstance(agent_ref, str) and version_ok:
            return RollbackAgentVersionCall(agent_ref, cast(int, version))
    return None


def _find_list_agents_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_schedules_call, for the argument-less
    list_agents tool (tools/builtin/agents_teams.py, #190)."""
    return any(tool_call.tool_name == "list_agents" for tool_call in run_output.tools or [])


class CreateTeamCall:
    """Parsed args for a create_team tool call (#190)."""

    def __init__(self, name: str, args: dict[str, object]) -> None:
        self.name = name
        self.description = _str_or_none(args, "description")


def _find_create_team_call(run_output: RunOutput) -> CreateTeamCall | None:
    """Same shape as _find_run_workflow_call, for the create_team tool
    (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "create_team":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        name = args.get("name")
        if isinstance(name, str):
            return CreateTeamCall(name, args)
    return None


class UpdateTeamCall:
    """Parsed args for an update_team tool call (#190)."""

    def __init__(self, team_ref: str, args: dict[str, object]) -> None:
        self.team_ref = team_ref
        self.name = _str_or_none(args, "name")
        self.description = _str_or_none(args, "description")
        self.agent_ids = _str_list(args, "agent_ids")


def _find_update_team_call(run_output: RunOutput) -> UpdateTeamCall | None:
    """Same shape as _find_run_workflow_call, for the update_team tool
    (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_team":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        team_ref = args.get("team")
        if isinstance(team_ref, str):
            return UpdateTeamCall(team_ref, args)
    return None


def _find_delete_team_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the delete_team tool
    (tools/builtin/agents_teams.py, #190)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "delete_team":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        team_ref = args.get("team")
        if isinstance(team_ref, str):
            return team_ref
    return None


def _find_list_teams_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_schedules_call, for the argument-less
    list_teams tool (tools/builtin/agents_teams.py, #190)."""
    return any(tool_call.tool_name == "list_teams" for tool_call in run_output.tools or [])


class RegisterMcpServerCall:
    """Parsed args for a register_mcp_server tool call (#191)."""

    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self.url = url


def _find_register_mcp_server_call(run_output: RunOutput) -> RegisterMcpServerCall | None:
    """Same shape as _find_run_workflow_call, for the register_mcp_server
    tool (tools/builtin/mcp_servers.py, #191)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "register_mcp_server":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        name = args.get("name")
        url = args.get("url")
        if isinstance(name, str) and isinstance(url, str):
            return RegisterMcpServerCall(name, url)
    return None


def _find_reconnect_mcp_server_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the
    reconnect_mcp_server tool (tools/builtin/mcp_servers.py, #191)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "reconnect_mcp_server":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        server_ref = args.get("server")
        if isinstance(server_ref, str):
            return server_ref
    return None


def _find_delete_mcp_server_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the delete_mcp_server
    tool (tools/builtin/mcp_servers.py, #191)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "delete_mcp_server":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        server_ref = args.get("server")
        if isinstance(server_ref, str):
            return server_ref
    return None


def _find_list_mcp_servers_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_schedules_call, for the argument-less
    list_mcp_servers tool (tools/builtin/mcp_servers.py, #191)."""
    return any(tool_call.tool_name == "list_mcp_servers" for tool_call in run_output.tools or [])


class CreateWorkflowCall:
    """Parsed args for a create_workflow tool call (#192)."""

    def __init__(self, name: str, args: dict[str, object]) -> None:
        self.name = name
        self.description = _str_or_none(args, "description")


def _find_create_workflow_call(run_output: RunOutput) -> CreateWorkflowCall | None:
    """Same shape as _find_run_workflow_call, for the create_workflow tool
    (tools/builtin/workflows.py, #192)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "create_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        name = args.get("name")
        if isinstance(name, str):
            return CreateWorkflowCall(name, args)
    return None


class UpdateWorkflowCall:
    """Parsed args for an update_workflow tool call (#192). Only
    `workflow_ref` (the id-or-name reference) is required;
    `name`/`description` fall back to None when the model didn't supply
    them, same as UpdateChannelCall above."""

    def __init__(self, workflow_ref: str, args: dict[str, object]) -> None:
        self.workflow_ref = workflow_ref
        self.name = _str_or_none(args, "name")
        self.description = _str_or_none(args, "description")


def _find_update_workflow_call(run_output: RunOutput) -> UpdateWorkflowCall | None:
    """Same shape as _find_run_workflow_call, for the update_workflow tool
    (tools/builtin/workflows.py, #192)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        workflow_ref = args.get("workflow")
        if isinstance(workflow_ref, str):
            return UpdateWorkflowCall(workflow_ref, args)
    return None


def _find_delete_workflow_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the delete_workflow
    tool (tools/builtin/workflows.py, #192)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "delete_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        workflow_ref = args.get("workflow")
        if isinstance(workflow_ref, str):
            return workflow_ref
    return None


def _find_publish_workflow_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the publish_workflow
    tool (tools/builtin/workflows.py, #192)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "publish_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        workflow_ref = args.get("workflow")
        if isinstance(workflow_ref, str):
            return workflow_ref
    return None


def _find_unpublish_workflow_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the
    unpublish_workflow tool (tools/builtin/workflows.py, #192)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "unpublish_workflow":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        workflow_ref = args.get("workflow")
        if isinstance(workflow_ref, str):
            return workflow_ref
    return None


def _find_list_workflows_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_schedules_call, for the argument-less
    list_workflows tool (tools/builtin/workflows.py, #192)."""
    return any(tool_call.tool_name == "list_workflows" for tool_call in run_output.tools or [])


def _find_get_workspace_settings_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_workflows_call, for the argument-less
    get_workspace_settings tool (tools/builtin/settings.py, #193)."""
    return any(
        tool_call.tool_name == "get_workspace_settings" for tool_call in run_output.tools or []
    )


def _find_update_workspace_settings_call(run_output: RunOutput) -> dict[str, object] | None:
    """Same shape as _find_update_agent_routing_rules_call, for the
    update_workspace_settings tool (tools/builtin/settings.py, #193).
    `settings` must be a dict to even be considered a call worth
    handling -- each key's validity against the known-settings catalog is
    checked later, in _handle_update_workspace_settings_trigger."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "update_workspace_settings":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        settings = args.get("settings")
        if isinstance(settings, dict):
            return cast(dict[str, object], settings)
    return None


class CreateInviteCall:
    """Parsed args for a create_invite tool call (#193). Every field is
    optional -- falls back to the same defaults InviteCreate (api/
    invites.py) uses when the model didn't supply them."""

    def __init__(self, args: dict[str, object]) -> None:
        self.display_name_hint = _str_or_none(args, "display_name_hint")
        raw_max_uses = args.get("max_uses", 1)
        self.max_uses = raw_max_uses if isinstance(raw_max_uses, int) else 1
        raw_expires_in_hours = args.get("expires_in_hours", 168)
        self.expires_in_hours = (
            raw_expires_in_hours if isinstance(raw_expires_in_hours, int) else 168
        )


def _find_create_invite_call(run_output: RunOutput) -> CreateInviteCall | None:
    """Same shape as _find_run_workflow_call, for the create_invite tool
    (tools/builtin/invites.py, #193). Unlike most create_* tools, every
    argument is optional, so any actual create_invite call -- even with
    an empty args dict -- is handled."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "create_invite":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        return CreateInviteCall(args)
    return None


def _find_list_invites_call(run_output: RunOutput) -> bool:
    """Same shape as _find_list_workflows_call, for the argument-less
    list_invites tool (tools/builtin/invites.py, #193)."""
    return any(tool_call.tool_name == "list_invites" for tool_call in run_output.tools or [])


def _find_revoke_invite_call(run_output: RunOutput) -> str | None:
    """Same shape as _find_cancel_schedule_call, for the revoke_invite
    tool (tools/builtin/invites.py, #193)."""
    for tool_call in run_output.tools or []:
        if tool_call.tool_name != "revoke_invite":
            continue
        args: dict[str, object] = tool_call.tool_args or {}
        invite_id = args.get("invite_id")
        if isinstance(invite_id, str):
            return invite_id
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
    new Message rows -- already committed individually as they're produced
    (#237: each agent's reply, and the guard/budget bookkeeping around it,
    is its own short transaction so no write lock spans an LLM call), not
    left for the caller to batch into one big commit at the end. Callers
    still commit after this returns, both as a final catch-all for
    whatever's pending (e.g. a trailing span close) and because SQLAlchemy
    objects like `rivulet` may have been mutated (e.g. a guard pause) since
    the caller's own last commit.

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

    # #237: engine.dispatch can itself invoke an LLM (its own hybrid-
    # routing fallback) -- commit whatever's pending (e.g. a freshly
    # created RivuletGuardState row) first so that call never runs with an
    # open write transaction behind it.
    await db.commit()
    engine = DispatchEngine(llm_fallback=build_llm_fallback(db))
    result = await engine.dispatch(message_content, dispatch_infos, speaker_id=from_agent_id)
    # A channel with a team is supposed to answer without an @mention
    # (README, FR-4.1). Mentions / rules / the LLM fallback still win
    # when they match; this only fills the "nobody claimed it" hole, and
    # only for a human message — applying it to recursive agent replies
    # would make the default teammate bounce on every response.
    if not result.agent_ids and from_agent_id is None:
        if default_id := engine.pick_default_teammate(dispatch_infos):
            result = DispatchResult(
                agent_ids=[default_id],
                method=DispatchMethod.DEFAULT,
                llm_invoked=result.llm_invoked,
            )
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

    # #237: the DispatchDecision/span/agentos_session_id writes above are
    # still only flushed, not committed -- close that out before the loop
    # below, whose first iteration might go straight to
    # _dispatch_to_remote_peer's RPC (_invoke_agent commits before its own
    # LLM call, but a remote-routed agent never reaches _invoke_agent).
    await db.commit()

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

    # #406: dispatch-none used to look like a successful delivery — run
    # Completed, composer still saying the team would answer, nothing in
    # the thread. Tell the human nobody picked this up.
    if not result.agent_ids and from_agent_id is None:
        new_messages.append(await _post_unrouted_notice(db, rivulet, channel, team_agents))

    await finish_span(db, dispatch_span_id, status="completed")
    # #237: this closes out a recursive call's own dispatch_decision span
    # promptly rather than leaving it dangling for whatever the caller
    # does next -- a caller further up the recursive chain (e.g. an
    # _invoke_agent about to run a workflow trigger, itself LLM-bound) may
    # not otherwise commit before its own next network-bound call. A
    # top-level call's own caller already commits again right after this
    # returns, so this is a no-op there.
    await db.commit()
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
_RUN_ERROR_LIMIT = 300


def _sanitize_run_error(text: str, *, limit: int = _RUN_ERROR_LIMIT) -> str:
    """#405: collapse whitespace and cap length so Runs detail can show
    `str(exc)` without dumping a stack-shaped wall of text into the
    page. The rivulet thread still uses a plain-language system_alert
    (NFR-5.4) and never displays this string."""
    cleaned = " ".join(text.split())
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned


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


async def _post_unrouted_notice(
    db: AsyncSession,
    rivulet: Rivulet,
    channel: Channel,
    team_agents: list[tuple[Agent, AgentDispatchInfo]],
) -> Message:
    """#406: visible system line when a human message matches nobody
    (and the default-teammate fallback also had no one eligible)."""
    team = await db.get(Team, channel.team_id) if channel.team_id is not None else None
    team_name = team.name if team is not None else "this team"
    suggest = next(
        (agent.name for agent, _ in team_agents if agent.name.lower() == "assistant"),
        None,
    )
    if suggest is None and team_agents:
        suggest = team_agents[0][0].name
    mention = f"@{suggest}" if suggest else "@someone"
    text = f"Nobody on {team_name} picked this up. Try {mention}, or change When to speak."
    message = Message(
        rivulet_id=rivulet.id,
        sender_type="system",
        sender_name="system",
        content=text,
        content_type="system_alert",
    )
    db.add(message)
    publish(
        rivulet.id,
        "system_alert",
        {"type": "unrouted", "message": text, "team_name": team_name},
    )
    return message


_GuardSnapshot = tuple[int, str | None, str | None, bool, str | None, str | None]


def _snapshot_guard_state(state: RivuletGuardState) -> _GuardSnapshot:
    """#237: captures the fields record_agent_message mutates, so a
    speculative pre-LLM-call reservation (see _invoke_agent) can be undone
    if the call turns out to have failed."""
    return (
        state.agent_exchange_count,
        state.recent_interactions,
        state.agent_active_since,
        state.paused,
        state.paused_at,
        state.pause_reason,
    )


def _restore_guard_state(state: RivuletGuardState, snapshot: _GuardSnapshot) -> None:
    (
        state.agent_exchange_count,
        state.recent_interactions,
        state.agent_active_since,
        state.paused,
        state.paused_at,
        state.pause_reason,
    ) = snapshot


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

    # #237: BEGIN IMMEDIATE takes SQLite's write lock for this section up
    # front (see db/session.py's begin_immediate) so a concurrent
    # invocation of the same rivulet can't pass its own budget/guard check
    # against pre-increment state while this one's LLM call (below) is
    # still in flight -- the commit a few lines down drops the lock before
    # that call runs.
    await begin_immediate(db)
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
        await db.commit()
        return new_messages

    # #237: reserve this turn's guard-state slot now, before the LLM call,
    # not after it returns -- otherwise a concurrent invocation of the same
    # rivulet can read this same (not-yet-incremented) state and also pass
    # its own check while this call is in flight. `pause_message` isn't
    # applied (rivulet.status, db.add) until the run actually succeeds
    # below, matching the original ordering; `guard_snapshot` lets a failed
    # run undo the reservation, since a provider error shouldn't count
    # toward FR-7's limits (it never really happened as far as the
    # conversation goes).
    guard_snapshot = _snapshot_guard_state(guard_state)
    pause_message = await record_agent_message(
        db,
        rivulet.id,
        guard_state,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name or "",
        to_agent_id=agent.id,
        to_agent_name=agent.name,
    )

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
    # #237: drop the write lock -- everything below through the LLM call
    # runs without an open transaction.
    await db.commit()
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
        model_tier = await classify_tier(db, agent, message_content)
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
        # #405: this path used to log + SSE `error` only, so the thread
        # showed dead air after the human message. Persist the same
        # system_alert shape as the RunStatus.error branch below; POST
        # is synchronous, so the refetch after send picks it up without
        # relying on SSE. The sanitized exception rides the RunSpan so
        # Runs detail can show why.
        assert exc is not None
        logger.warning(
            "Agent %r failed to respond in rivulet %r", agent.name, rivulet.id, exc_info=exc
        )
        reason = _sanitize_run_error(str(exc))
        publish(rivulet.id, "error", {"agent_id": agent.id, "error": reason})
        _restore_guard_state(guard_state, guard_snapshot)  # #237: failed call, undo the reservation
        await finish_span(db, agent_span_id, status="error", error_message=reason)
        message = Message(
            rivulet_id=rivulet.id,
            sender_type="system",
            sender_name="system",
            content=f"{agent.name} couldn't respond — it failed before a run started.",
            content_type="system_alert",
        )
        db.add(message)
        new_messages.append(message)
        await db.commit()
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
        reason = _sanitize_run_error(str(run_output.content))
        publish(rivulet.id, "error", {"agent_id": agent.id, "error": reason})
        _restore_guard_state(guard_state, guard_snapshot)  # #237: failed call, undo the reservation
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
            error_message=reason,
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
        await db.commit()
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
    # #237: commit the reply now rather than holding it open through the
    # handoff/tool-trigger/recursion cascade below (which can itself
    # invoke further LLM calls, e.g. a run_workflow trigger). Also
    # populates message.id for the agent_message event below (commit
    # implies a flush; expire_on_commit=False keeps it readable after).
    await db.commit()
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
        await db.commit()
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

    new_messages.extend(
        await apply_builtin_tool_triggers(db, rivulet, agent, run_output, trace_ctx=child_trace_ctx)
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


async def apply_builtin_tool_triggers(
    db: AsyncSession,
    rivulet: Rivulet,
    agent: Agent,
    run_output: RunOutput,
    *,
    trace_ctx: TraceContext | None = None,
    workflow_ancestry: frozenset[str] = frozenset(),
    unattended: bool | None = None,
) -> list[Message]:
    """Inspect a completed run's tool calls and actually perform every
    builtin side-effect tool the agent invoked (the tools/builtin/ stubs
    themselves are deliberately side-effect-free — see run_workflow.py's
    module docstring). Extracted from _invoke_agent for #360 so
    workflows/nodes.py's execute_agent_node can reuse it: before that, an
    agent used as a workflow 'agent' node could *say* it ran another
    workflow (or created a channel, published a workflow, ...) while
    nothing actually happened — the trigger handlers below only ever ran
    on the channel-dispatch path. Handoff is NOT handled here: it needs
    channel/guard/team context a workflow agent node doesn't have, so it
    stays in _invoke_agent.

    Returns the Messages the handlers staged on `db` (confirmations,
    rejections, listings). The channel-dispatch caller returns them up to
    api/rivulets.py, which commits/publishes the whole batch;
    execute_agent_node owns its own commit/publish instead (the engine's
    self-contained _post_message pattern).

    `workflow_ancestry`/`unattended` are only passed by the workflow-node
    caller: ancestry feeds _handle_run_workflow_trigger's cycle/depth
    guard (an agent node triggering run_workflow is a nested run, same as
    a 'workflow' node, and needs the same #85/#249 protection), and
    unattended is inherited by the triggered child run rather than being
    re-derived from its literal 'agent' trigger (the same reasoning as
    _execute_workflow_node's explicit pass-through, #100)."""
    new_messages: list[Message] = []

    run_workflow_call = _find_run_workflow_call(run_output)
    if run_workflow_call is not None:
        workflow_name, workflow_input = run_workflow_call
        await _handle_run_workflow_trigger(
            db,
            rivulet,
            agent,
            workflow_name,
            workflow_input,
            trace_ctx=trace_ctx,
            ancestry=workflow_ancestry,
            unattended=unattended,
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

    create_channel_call = _find_create_channel_call(run_output)
    if create_channel_call is not None and await _authorize_builtin_call(
        db, agent, "create_channel"
    ):
        new_messages.extend(
            await _handle_create_channel_trigger(db, rivulet, agent, create_channel_call)
        )

    update_channel_call = _find_update_channel_call(run_output)
    if update_channel_call is not None and await _authorize_builtin_call(
        db, agent, "update_channel"
    ):
        new_messages.extend(
            await _handle_update_channel_trigger(db, rivulet, agent, update_channel_call)
        )

    archive_channel_call = _find_archive_channel_call(run_output)
    if archive_channel_call is not None and await _authorize_builtin_call(
        db, agent, "archive_channel"
    ):
        new_messages.extend(
            await _handle_archive_channel_trigger(db, rivulet, agent, archive_channel_call)
        )

    unarchive_channel_call = _find_unarchive_channel_call(run_output)
    if unarchive_channel_call is not None and await _authorize_builtin_call(
        db, agent, "unarchive_channel"
    ):
        new_messages.extend(
            await _handle_unarchive_channel_trigger(db, rivulet, agent, unarchive_channel_call)
        )

    reorder_channels_call = _find_reorder_channels_call(run_output)
    if reorder_channels_call is not None and await _authorize_builtin_call(
        db, agent, "reorder_channels"
    ):
        new_messages.extend(
            await _handle_reorder_channels_trigger(db, rivulet, agent, reorder_channels_call)
        )

    if _find_list_channels_call(run_output):
        new_messages.extend(await _handle_list_channels_trigger(db, rivulet, agent))

    create_agent_call = _find_create_agent_call(run_output)
    if create_agent_call is not None and await _authorize_builtin_call(db, agent, "create_agent"):
        new_messages.extend(
            await _handle_create_agent_trigger(db, rivulet, agent, create_agent_call)
        )

    update_agent_call = _find_update_agent_call(run_output)
    if update_agent_call is not None and await _authorize_builtin_call(db, agent, "update_agent"):
        new_messages.extend(
            await _handle_update_agent_trigger(db, rivulet, agent, update_agent_call)
        )

    delete_agent_call = _find_delete_agent_call(run_output)
    if delete_agent_call is not None and await _authorize_builtin_call(db, agent, "delete_agent"):
        new_messages.extend(
            await _handle_delete_agent_trigger(db, rivulet, agent, delete_agent_call)
        )

    update_agent_routing_rules_call = _find_update_agent_routing_rules_call(run_output)
    if update_agent_routing_rules_call is not None and await _authorize_builtin_call(
        db, agent, "update_agent_routing_rules"
    ):
        new_messages.extend(
            await _handle_update_agent_routing_rules_trigger(
                db, rivulet, agent, update_agent_routing_rules_call
            )
        )

    update_agent_peer_preference_call = _find_update_agent_peer_preference_call(run_output)
    if update_agent_peer_preference_call is not None and await _authorize_builtin_call(
        db, agent, "update_agent_peer_preference"
    ):
        new_messages.extend(
            await _handle_update_agent_peer_preference_trigger(
                db, rivulet, agent, update_agent_peer_preference_call
            )
        )

    rollback_agent_version_call = _find_rollback_agent_version_call(run_output)
    if rollback_agent_version_call is not None and await _authorize_builtin_call(
        db, agent, "rollback_agent_version"
    ):
        new_messages.extend(
            await _handle_rollback_agent_version_trigger(
                db, rivulet, agent, rollback_agent_version_call
            )
        )

    if _find_list_agents_call(run_output):
        new_messages.extend(await _handle_list_agents_trigger(db, rivulet, agent))

    create_team_call = _find_create_team_call(run_output)
    if create_team_call is not None and await _authorize_builtin_call(db, agent, "create_team"):
        new_messages.extend(await _handle_create_team_trigger(db, rivulet, agent, create_team_call))

    update_team_call = _find_update_team_call(run_output)
    if update_team_call is not None and await _authorize_builtin_call(db, agent, "update_team"):
        new_messages.extend(await _handle_update_team_trigger(db, rivulet, agent, update_team_call))

    delete_team_call = _find_delete_team_call(run_output)
    if delete_team_call is not None and await _authorize_builtin_call(db, agent, "delete_team"):
        new_messages.extend(await _handle_delete_team_trigger(db, rivulet, agent, delete_team_call))

    if _find_list_teams_call(run_output):
        new_messages.extend(await _handle_list_teams_trigger(db, rivulet, agent))

    register_mcp_server_call = _find_register_mcp_server_call(run_output)
    if register_mcp_server_call is not None and await _authorize_builtin_call(
        db, agent, "register_mcp_server"
    ):
        new_messages.extend(
            await _handle_register_mcp_server_trigger(db, rivulet, agent, register_mcp_server_call)
        )

    reconnect_mcp_server_call = _find_reconnect_mcp_server_call(run_output)
    if reconnect_mcp_server_call is not None and await _authorize_builtin_call(
        db, agent, "reconnect_mcp_server"
    ):
        new_messages.extend(
            await _handle_reconnect_mcp_server_trigger(
                db, rivulet, agent, reconnect_mcp_server_call
            )
        )

    delete_mcp_server_call = _find_delete_mcp_server_call(run_output)
    if delete_mcp_server_call is not None and await _authorize_builtin_call(
        db, agent, "delete_mcp_server"
    ):
        new_messages.extend(
            await _handle_delete_mcp_server_trigger(db, rivulet, agent, delete_mcp_server_call)
        )

    if _find_list_mcp_servers_call(run_output):
        new_messages.extend(await _handle_list_mcp_servers_trigger(db, rivulet, agent))

    create_workflow_call = _find_create_workflow_call(run_output)
    if create_workflow_call is not None and await _authorize_builtin_call(
        db, agent, "create_workflow"
    ):
        new_messages.extend(
            await _handle_create_workflow_trigger(db, rivulet, agent, create_workflow_call)
        )

    update_workflow_call = _find_update_workflow_call(run_output)
    if update_workflow_call is not None and await _authorize_builtin_call(
        db, agent, "update_workflow"
    ):
        new_messages.extend(
            await _handle_update_workflow_trigger(db, rivulet, agent, update_workflow_call)
        )

    delete_workflow_call = _find_delete_workflow_call(run_output)
    if delete_workflow_call is not None and await _authorize_builtin_call(
        db, agent, "delete_workflow"
    ):
        new_messages.extend(
            await _handle_delete_workflow_trigger(db, rivulet, agent, delete_workflow_call)
        )

    publish_workflow_call = _find_publish_workflow_call(run_output)
    if publish_workflow_call is not None and await _authorize_builtin_call(
        db, agent, "publish_workflow"
    ):
        new_messages.extend(
            await _handle_publish_workflow_trigger(db, rivulet, agent, publish_workflow_call)
        )

    unpublish_workflow_call = _find_unpublish_workflow_call(run_output)
    if unpublish_workflow_call is not None and await _authorize_builtin_call(
        db, agent, "unpublish_workflow"
    ):
        new_messages.extend(
            await _handle_unpublish_workflow_trigger(db, rivulet, agent, unpublish_workflow_call)
        )

    if _find_list_workflows_call(run_output):
        new_messages.extend(await _handle_list_workflows_trigger(db, rivulet, agent))

    if _find_get_workspace_settings_call(run_output) and await _authorize_builtin_call(
        db, agent, "get_workspace_settings"
    ):
        new_messages.extend(await _handle_get_workspace_settings_trigger(db, rivulet, agent))

    update_workspace_settings_call = _find_update_workspace_settings_call(run_output)
    if update_workspace_settings_call is not None and await _authorize_builtin_call(
        db, agent, "update_workspace_settings"
    ):
        new_messages.extend(
            await _handle_update_workspace_settings_trigger(
                db, rivulet, agent, update_workspace_settings_call
            )
        )

    create_invite_call = _find_create_invite_call(run_output)
    if create_invite_call is not None and await _authorize_builtin_call(db, agent, "create_invite"):
        new_messages.extend(
            await _handle_create_invite_trigger(db, rivulet, agent, create_invite_call)
        )

    if _find_list_invites_call(run_output) and await _authorize_builtin_call(
        db, agent, "list_invites"
    ):
        new_messages.extend(await _handle_list_invites_trigger(db, rivulet, agent))

    revoke_invite_call = _find_revoke_invite_call(run_output)
    if revoke_invite_call is not None and await _authorize_builtin_call(db, agent, "revoke_invite"):
        new_messages.extend(
            await _handle_revoke_invite_trigger(db, rivulet, agent, revoke_invite_call)
        )

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
    ancestry: frozenset[str] = frozenset(),
    unattended: bool | None = None,
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
    _post_message).

    `ancestry`/`unattended` (#360) are only ever non-default when this
    trigger fired from a workflow 'agent' node (apply_builtin_tool_triggers'
    docstring): the triggered run is then a *nested* run, so it gets the
    same cycle and nesting-depth guards _execute_workflow_node applies to
    a 'workflow' node — without them, a workflow whose agent node
    run_workflow's the workflow itself would recurse unboundedly, since
    engine.py's guards only cover the 'workflow' node path. Rejections
    stay log-only, matching this handler's existing silent treatment of
    an unknown workflow name."""
    from rivulets.workflows import find_workflow_by_name, run_workflow
    from rivulets.workflows.engine import MAX_WORKFLOW_NESTING_DEPTH

    workflow = await find_workflow_by_name(db, workflow_name)
    if workflow is None:
        logger.warning(
            "Agent %r tried to run unknown workflow %r in rivulet %r",
            agent.name,
            workflow_name,
            rivulet.id,
        )
        return
    if workflow.id in ancestry:
        logger.warning(
            "Agent %r tried to run workflow %r from inside that same workflow "
            "(cycle) in rivulet %r — skipping",
            agent.name,
            workflow_name,
            rivulet.id,
        )
        return
    if len(ancestry) >= MAX_WORKFLOW_NESTING_DEPTH:
        logger.warning(
            "Agent %r tried to run workflow %r more than %d workflow levels deep "
            "in rivulet %r — skipping",
            agent.name,
            workflow_name,
            MAX_WORKFLOW_NESTING_DEPTH,
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
        ancestry=ancestry,
        trace_ctx=trace_ctx,
        unattended=unattended,
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


_CHANNEL_NAME_MIN_LEN = 3
_CHANNEL_NAME_MAX_LEN = 80


async def _resolve_channel_ref(
    db: AsyncSession, channel_ref: str
) -> Channel | list[Channel] | None:
    """Resolves a channel tool's `channel` arg, which may be the
    channel's id or its name. An id match (the primary key) is checked
    first and is always unambiguous. Name is only unique among
    non-archived channels (db/models.py's idx_channel_name), so a name
    match can, in principle, hit more than one archived channel sharing
    a name -- returned as a list so callers can report that ambiguity
    the same way _handle_cancel_schedule_trigger does for schedule
    names, rather than silently acting on whichever one sorts first."""
    channel = await db.get(Channel, channel_ref)
    if channel is not None:
        return channel
    matches = list((await db.scalars(select(Channel).where(Channel.name == channel_ref))).all())
    if len(matches) == 1:
        return matches[0]
    if matches:
        return matches
    return None


async def _handle_create_channel_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: CreateChannelCall
) -> list[Message]:
    """#189: an agent called the create_channel tool. Validation mirrors
    api/channels.py's create_channel handler (name length, name
    uniqueness among non-archived channels via idx_channel_name) so a
    request that would 400 through the API is rejected the same way
    here, as a visible message rather than an unhandled IntegrityError."""
    if not (_CHANNEL_NAME_MIN_LEN <= len(call.name) <= _CHANNEL_NAME_MAX_LEN):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create channel {call.name!r}, but channel names must "
                f"be {_CHANNEL_NAME_MIN_LEN}-{_CHANNEL_NAME_MAX_LEN} characters.",
            )
        ]
    existing = (
        await db.scalars(
            select(Channel).where(Channel.name == call.name, Channel.archived.is_(False))
        )
    ).first()
    if existing is not None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create channel {call.name!r}, but a channel with that "
                "name already exists.",
            )
        ]

    channel = Channel(name=call.name, description=call.description)
    db.add(channel)
    await db.flush()
    await publish_current_state(db, "channel", channel.id)
    message = _system_message(
        db, rivulet, f"@{agent.name} created channel /{channel.name} (id: {channel.id})."
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "channel_created", "channel_id": channel.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_update_channel_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: UpdateChannelCall
) -> list[Message]:
    """#189: an agent called the update_channel tool. Mirrors api/
    channels.py's update_channel handler (name length + uniqueness
    checks, updated_at/vector_clock bump)."""
    result = await _resolve_channel_ref(db, call.channel_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update channel {call.channel_ref!r}, but no channel "
                "with that id or name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(c.id for c in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update channel {call.channel_ref!r}, but that name "
                f"matches {len(result)} channels ({ids}) -- specify by id instead.",
            )
        ]
    channel = result

    if call.name is None and call.description is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update channel /{channel.name}, but didn't specify "
                "any changes.",
            )
        ]

    if call.name is not None:
        if not (_CHANNEL_NAME_MIN_LEN <= len(call.name) <= _CHANNEL_NAME_MAX_LEN):
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to rename channel /{channel.name}, but channel names "
                    f"must be {_CHANNEL_NAME_MIN_LEN}-{_CHANNEL_NAME_MAX_LEN} characters.",
                )
            ]
        if call.name != channel.name:
            conflict = (
                await db.scalars(
                    select(Channel).where(Channel.name == call.name, Channel.archived.is_(False))
                )
            ).first()
            if conflict is not None:
                return [
                    _system_message(
                        db,
                        rivulet,
                        f"@{agent.name} tried to rename channel /{channel.name} to "
                        f"{call.name!r}, but a channel with that name already exists.",
                    )
                ]
        previous_name = channel.name
        channel.name = call.name
    else:
        previous_name = channel.name

    if call.description is not None:
        channel.description = call.description

    channel.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    channel.vector_clock += 1
    await db.flush()
    await publish_current_state(db, "channel", channel.id)
    message = _system_message(db, rivulet, f"@{agent.name} updated channel /{previous_name}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "channel_updated", "channel_id": channel.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_archive_channel_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, channel_ref: str
) -> list[Message]:
    """#189: an agent called the archive_channel tool. Mirrors api/
    channels.py's archive_channel handler (soft delete, FR-2.5)."""
    result = await _resolve_channel_ref(db, channel_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to archive channel {channel_ref!r}, but no channel with "
                "that id or name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(c.id for c in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to archive channel {channel_ref!r}, but that name matches "
                f"{len(result)} channels ({ids}) -- specify by id instead.",
            )
        ]
    channel = result
    if channel.archived:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to archive channel /{channel.name}, but it's already "
                "archived.",
            )
        ]

    channel.archived = True
    await db.flush()
    await publish_current_state(db, "channel", channel.id)
    message = _system_message(db, rivulet, f"@{agent.name} archived channel /{channel.name}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "channel_archived", "channel_id": channel.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_unarchive_channel_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, channel_ref: str
) -> list[Message]:
    """#189: an agent called the unarchive_channel tool. Mirrors api/
    channels.py's unarchive_channel handler. Unlike archive_channel,
    a name match here can genuinely be ambiguous (idx_channel_name only
    enforces uniqueness while archived=0, so more than one archived
    channel can share a name) -- _resolve_channel_ref already surfaces
    that as a list rather than picking one."""
    result = await _resolve_channel_ref(db, channel_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to unarchive channel {channel_ref!r}, but no channel with "
                "that id or name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(c.id for c in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to unarchive channel {channel_ref!r}, but that name "
                f"matches {len(result)} channels ({ids}) -- specify by id instead.",
            )
        ]
    channel = result
    if not channel.archived:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to unarchive channel /{channel.name}, but it isn't archived.",
            )
        ]

    channel.archived = False
    await db.flush()
    await publish_current_state(db, "channel", channel.id)
    message = _system_message(db, rivulet, f"@{agent.name} unarchived channel /{channel.name}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "channel_unarchived", "channel_id": channel.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_reorder_channels_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, order: list[str]
) -> list[Message]:
    """#189: an agent called the reorder_channels tool. Mirrors api/
    channels.py's reorder_channels handler (full-list reorder, not a
    single-item move) -- every ref in `order` must resolve to exactly
    one channel before any position is actually changed, so a bad
    request doesn't leave channels half-reordered."""
    channels: list[Channel] = []
    for ref in order:
        result = await _resolve_channel_ref(db, ref)
        if result is None:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to reorder channels, but {ref!r} doesn't match any "
                    "channel.",
                )
            ]
        if isinstance(result, list):
            ids = ", ".join(c.id for c in result)
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to reorder channels, but {ref!r} matches "
                    f"{len(result)} channels ({ids}) -- specify by id instead.",
                )
            ]
        channels.append(result)

    for position, channel in enumerate(channels):
        channel.position = position
    await db.flush()
    for channel in channels:
        await publish_current_state(db, "channel", channel.id)

    message = _system_message(db, rivulet, f"@{agent.name} reordered {len(channels)} channels.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "channels_reordered", "agent_id": agent.id},
    )
    return [message]


async def _handle_list_channels_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#189: an agent called the (unscoped, read-only) list_channels
    tool."""
    channels = list((await db.scalars(select(Channel).order_by(Channel.position))).all())
    if not channels:
        content = f"@{agent.name} looked up the workspace's channels: there are none yet."
    else:
        lines = [f"@{agent.name} looked up the workspace's channels:"]
        for c in channels:
            status = " (archived)" if c.archived else ""
            lines.append(f"- /{c.name}{status} (id: {c.id})")
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


_AGENT_NAME_MIN_LEN = 2
_AGENT_NAME_MAX_LEN = 64
_AGENT_DESCRIPTION_MIN_LEN = 10
_AGENT_DESCRIPTION_MAX_LEN = 500

_ROUTING_RULE_TYPES = {rt.value for rt in RuleType}
_ARRAY_PATTERN_RULE_TYPES = {RuleType.KEYWORD.value, RuleType.SEMANTIC.value}


async def _resolve_agent_ref(db: AsyncSession, agent_ref: str) -> Agent | None:
    """Resolves an agent tool's `agent` arg, which may be the agent's id
    or its name. An id match is checked first. Unlike
    _resolve_channel_ref, a name match is never ambiguous -- Agent.name
    is globally unique (db/models.py's idx_agent_name), not just unique
    among some subset of rows -- so there's no list-of-matches case to
    handle here."""
    agent = await db.get(Agent, agent_ref)
    if agent is not None:
        return agent
    return await db.scalar(select(Agent).where(Agent.name == agent_ref))


async def _resolve_team_ref(db: AsyncSession, team_ref: str) -> Team | list[Team] | None:
    """Resolves a team tool's `team` arg, which may be the team's id or
    its name. An id match is checked first and is always unambiguous.
    Unlike Agent.name, Team.name carries no uniqueness constraint at all
    (db/models.py's Team), so a name match can genuinely hit more than
    one team -- returned as a list so callers can report that ambiguity
    the same way _resolve_channel_ref does, rather than silently acting
    on whichever one sorts first."""
    team = await db.get(Team, team_ref)
    if team is not None:
        return team
    matches = list((await db.scalars(select(Team).where(Team.name == team_ref))).all())
    if len(matches) == 1:
        return matches[0]
    if matches:
        return matches
    return None


def _parse_routing_rule(item: dict[str, object]) -> tuple[str, str, int] | None:
    """Validates and normalizes one rule dict from an
    update_agent_routing_rules tool call into (rule_type, pattern,
    priority), ready for an AgentRoutingRule row. `pattern` is
    JSON-encoded for keyword/semantic rules (matching how _row_to_rule
    expects to read it back out) since the tool itself accepts a real
    list for those, not a pre-encoded JSON string -- more natural for a
    model to produce than asking it to JSON-encode a string field the
    way RoutingRuleIn's HTTP shape does. Returns None for anything
    malformed; the caller decides how to report that."""
    rule_type = item.get("rule_type")
    if not isinstance(rule_type, str) or rule_type not in _ROUTING_RULE_TYPES:
        return None
    raw_priority = item.get("priority", 0)
    priority_is_int = isinstance(raw_priority, int) and not isinstance(raw_priority, bool)
    priority = cast(int, raw_priority) if priority_is_int else 0
    raw_pattern = item.get("pattern")
    if rule_type in _ARRAY_PATTERN_RULE_TYPES:
        if isinstance(raw_pattern, list) and all(
            isinstance(p, str) for p in cast(list[Any], raw_pattern)
        ):
            pattern = json.dumps(raw_pattern)
        else:
            return None
    elif rule_type == RuleType.REGEX.value:
        if not isinstance(raw_pattern, str) or not is_valid_regex(raw_pattern):
            return None
        pattern = raw_pattern
    else:
        pattern = ""
    return rule_type, pattern, priority


async def _handle_create_agent_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: CreateAgentCall
) -> list[Message]:
    """#190: an agent called the create_agent tool. Validation mirrors
    api/agents.py's AgentCreate constraints (name/description length)
    plus proactive name-uniqueness and team_ids-existence checks the HTTP
    layer itself leaves to the DB's own constraints (idx_agent_name,
    team.id's FK) -- a request that would 500 through the API is
    rejected here as a visible message instead, the same defensive
    posture _handle_create_channel_trigger already takes. On success,
    gives the new agent the full "actually usable" treatment a human's
    own API call gets: an initial #104 version snapshot, LLM-generated
    routing rules (FR-3.3), and AgentOS registration (FR-3.2) -- skipping
    any of these would leave a new agent that exists in the DB but can
    never actually be dispatched to."""
    if not (_AGENT_NAME_MIN_LEN <= len(call.name) <= _AGENT_NAME_MAX_LEN):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create agent {call.name!r}, but agent names must be "
                f"{_AGENT_NAME_MIN_LEN}-{_AGENT_NAME_MAX_LEN} characters.",
            )
        ]
    existing = await db.scalar(select(Agent).where(Agent.name == call.name))
    if existing is not None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create agent {call.name!r}, but an agent with that "
                "name already exists.",
            )
        ]
    if not (_AGENT_DESCRIPTION_MIN_LEN <= len(call.description) <= _AGENT_DESCRIPTION_MAX_LEN):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create agent {call.name!r}, but its description must "
                f"be {_AGENT_DESCRIPTION_MIN_LEN}-{_AGENT_DESCRIPTION_MAX_LEN} characters.",
            )
        ]
    teams: list[Team] = []
    for team_id in call.team_ids:
        team = await db.get(Team, team_id)
        if team is None:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to create agent {call.name!r}, but team id "
                    f"{team_id!r} doesn't exist.",
                )
            ]
        teams.append(team)

    new_agent = Agent(
        name=call.name,
        description=call.description,
        instructions=call.instructions,
        model=call.model,
    )
    db.add(new_agent)
    await db.flush()  # populate new_agent.id before using it in join rows
    old_team_ids, new_team_ids = await set_agent_teams(db, new_agent.id, [t.id for t in teams])
    await record_agent_version(db, new_agent)
    await db.commit()

    await publish_agent_teams_change(db, new_agent.id, old_team_ids, new_team_ids)
    await generate_and_store_routing_rules(db, new_agent)
    await register_agent_with_agentos(db, new_agent)
    await publish_agent_change(db, new_agent)

    message = _system_message(
        db, rivulet, f"@{agent.name} created agent @{new_agent.name} (id: {new_agent.id})."
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "agent_created", "agent_id": new_agent.id, "created_by": agent.id},
    )
    return [message]


async def _handle_update_agent_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: UpdateAgentCall
) -> list[Message]:
    """#190: an agent called the update_agent tool. Mirrors api/agents.py's
    update_agent handler (name/description validation, routing-rule
    regen when description/instructions change, #104 version snapshot
    when instructions/model change, AgentOS re-registration) plus
    proactive tool_ids/team_ids-existence checks the HTTP layer itself
    leaves to the DB's FK constraints.

    #310: mirrors api/agents.py's update_agent -- an agent already holding
    an owner-granted capability scope is a confused-deputy risk for *any*
    edit here too, and this trigger has no live session to compare a
    claims.grant == "owner" bypass against (same "no live session to
    check" reasoning as _mcp_server_requires_owner_to_mutate's docstring),
    so it refuses outright rather than trying to enumerate every field
    that could steer a scoped agent."""
    result = await _resolve_agent_ref(db, call.agent_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update agent {call.agent_ref!r}, but no agent with "
                "that id or name was found.",
            )
        ]
    target = result

    if await agent_holds_owner_scope(db, target.id):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update agent @{target.name}, but it holds a capability "
                "scope -- that requires a live owner session in the UI, not just this chat.",
            )
        ]

    if (
        call.name is None
        and call.description is None
        and call.instructions is None
        and call.model is None
        and call.tool_ids is None
        and call.team_ids is None
    ):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update agent @{target.name}, but didn't specify any "
                "changes.",
            )
        ]

    if call.name is not None and call.name != target.name:
        if not (_AGENT_NAME_MIN_LEN <= len(call.name) <= _AGENT_NAME_MAX_LEN):
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to rename agent @{target.name}, but agent names must "
                    f"be {_AGENT_NAME_MIN_LEN}-{_AGENT_NAME_MAX_LEN} characters.",
                )
            ]
        conflict = await db.scalar(select(Agent).where(Agent.name == call.name))
        if conflict is not None:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to rename agent @{target.name} to {call.name!r}, but "
                    "an agent with that name already exists.",
                )
            ]

    if call.description is not None and not (
        _AGENT_DESCRIPTION_MIN_LEN <= len(call.description) <= _AGENT_DESCRIPTION_MAX_LEN
    ):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update agent @{target.name}, but its description must "
                f"be {_AGENT_DESCRIPTION_MIN_LEN}-{_AGENT_DESCRIPTION_MAX_LEN} characters.",
            )
        ]

    if call.tool_ids is not None:
        for tool_id in call.tool_ids:
            if await db.get(Tool, tool_id) is None:
                return [
                    _system_message(
                        db,
                        rivulet,
                        f"@{agent.name} tried to update agent @{target.name}, but tool id "
                        f"{tool_id!r} doesn't exist.",
                    )
                ]
        # #310: mirrors api/agents.py's update_agent -- assigning a custom/
        # MCP tool or one requiring a scope is owner-only blast radius
        # (find_unauthorized_tool_assignment's docstring); unconditional
        # here for the same no-live-session reason as the owner-scope
        # check above.
        unauthorized_tool = await find_unauthorized_tool_assignment(db, call.tool_ids)
        if unauthorized_tool is not None:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to assign tool {unauthorized_tool!r} to agent "
                    f"@{target.name}, but that requires a live owner session in the UI, not "
                    "just this chat.",
                )
            ]

    if call.team_ids is not None:
        for team_id in call.team_ids:
            if await db.get(Team, team_id) is None:
                return [
                    _system_message(
                        db,
                        rivulet,
                        f"@{agent.name} tried to update agent @{target.name}, but team id "
                        f"{team_id!r} doesn't exist.",
                    )
                ]

    previous_name = target.name
    rule_regen = call.description is not None or call.instructions is not None
    old_instructions, old_model = target.instructions, target.model

    if call.name is not None:
        target.name = call.name
    if call.description is not None:
        target.description = call.description
    if call.instructions is not None:
        target.instructions = call.instructions
    if call.model is not None:
        target.model = call.model
    tool_diff: tuple[set[str], set[str]] | None = None
    if call.tool_ids is not None:
        tool_diff = await set_agent_tools(db, target.id, call.tool_ids)
    team_diff: tuple[set[str], set[str]] | None = None
    if call.team_ids is not None:
        team_diff = await set_agent_teams(db, target.id, call.team_ids)

    if target.instructions != old_instructions or target.model != old_model:
        await record_agent_version(db, target)

    target.vector_clock += 1
    await db.commit()

    if tool_diff is not None:
        await publish_agent_tools_change(db, target.id, *tool_diff)
    if team_diff is not None:
        await publish_agent_teams_change(db, target.id, *team_diff)

    if rule_regen:
        await generate_and_store_routing_rules(db, target)

    await register_agent_with_agentos(db, target)
    await publish_agent_change(db, target)

    message = _system_message(db, rivulet, f"@{agent.name} updated agent @{previous_name}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "agent_updated", "agent_id": target.id, "updated_by": agent.id},
    )
    return [message]


async def _handle_delete_agent_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, agent_ref: str
) -> list[Message]:
    """#190: an agent called the delete_agent tool. Mirrors api/agents.py's
    delete_agent handler (delete + re-sync AgentOS's registry), plus a
    guard the HTTP layer doesn't need: this handler runs *during* the
    calling agent's own turn, so letting it delete itself would pull the
    row out from under the rest of this function's still-in-flight
    _invoke_agent call (guard bookkeeping, the recursive re-dispatch
    below, a possible handoff) -- refused up front instead of leaving
    that an untested edge case.

    #310: also mirrors api/agents.py's delete_agent -- a scoped agent is
    a confused-deputy risk, and this trigger has no live session to check
    a claims.grant == "owner" bypass against, so it refuses outright."""
    result = await _resolve_agent_ref(db, agent_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete agent {agent_ref!r}, but no agent with that id "
                "or name was found.",
            )
        ]
    target = result
    if target.id == agent.id:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete itself -- an agent can't delete itself while "
                "running.",
            )
        ]
    if await agent_holds_owner_scope(db, target.id):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete agent @{target.name}, but it holds a capability "
                "scope -- that requires a live owner session in the UI, not just this chat.",
            )
        ]
    target_name = target.name
    target_id = target.id
    await db.delete(target)
    await db.commit()
    await sync_agents(db)
    # #287: mirrors api/agents.py's delete_agent -- without this, an
    # agent-triggered delete (unlike the HTTP one) never told peers about
    # it, so a peer that still had the row would recreate it on its next
    # edit (#238's failure mode).
    await publish_tombstone(db, "agent", target_id)

    message = _system_message(db, rivulet, f"@{agent.name} deleted agent @{target_name}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "agent_deleted", "agent_name": target_name, "deleted_by": agent.id},
    )
    return [message]


async def _handle_update_agent_routing_rules_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: UpdateAgentRoutingRulesCall
) -> list[Message]:
    """#190: an agent called the update_agent_routing_rules tool. Mirrors
    api/agents.py's update_routing_rules handler (full replace, not a
    merge) plus proactive per-rule validation the HTTP layer leaves to
    Pydantic's RoutingRuleIn -- a rules list built by a model isn't
    guaranteed well-typed the way a validated request body is.

    #310: also mirrors update_routing_rules's agent_holds_owner_scope
    gate -- steering how a scoped agent gets selected is the same
    confused-deputy risk as rewriting its instructions, and this trigger
    has no live session to check a claims.grant == "owner" bypass
    against, so it refuses outright."""
    result = await _resolve_agent_ref(db, call.agent_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update routing rules for agent {call.agent_ref!r}, "
                "but no agent with that id or name was found.",
            )
        ]
    target = result

    if await agent_holds_owner_scope(db, target.id):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update routing rules for agent @{target.name}, but it "
                "holds a capability scope -- that requires a live owner session in the UI, not "
                "just this chat.",
            )
        ]

    parsed: list[tuple[str, str, int]] = []
    for item in call.rules:
        rule = _parse_routing_rule(item)
        if rule is None:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to update routing rules for agent @{target.name}, "
                    f"but {item!r} isn't a valid rule.",
                )
            ]
        parsed.append(rule)

    await replace_routing_rules(db, target.id, parsed)

    message = _system_message(
        db, rivulet, f"@{agent.name} set {len(parsed)} routing rule(s) for agent @{target.name}."
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "agent_routing_rules_updated", "agent_id": target.id, "updated_by": agent.id},
    )
    return [message]


async def _handle_update_agent_peer_preference_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: UpdateAgentPeerPreferenceCall
) -> list[Message]:
    """#190: an agent called the update_agent_peer_preference tool.
    Mirrors api/agents.py's set_peer_preference handler: capability_tag=
    None clears the existing preference and tombstones it (#311, same as
    that handler); setting a tag creates/updates the row and publishes it
    (#10, sync/apply.py's AGENT_PEER_PREFERENCE_SPEC).

    #310: also mirrors set_peer_preference's agent_holds_owner_scope
    gate -- steering which peer a scoped agent preferentially runs on is
    a subtler version of the same confused-deputy risk, and this trigger
    has no live session to check a claims.grant == "owner" bypass
    against, so it refuses outright."""
    result = await _resolve_agent_ref(db, call.agent_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to set peer preference for agent {call.agent_ref!r}, but "
                "no agent with that id or name was found.",
            )
        ]
    target = result
    if await agent_holds_owner_scope(db, target.id):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to set peer preference for agent @{target.name}, but it "
                "holds a capability scope -- that requires a live owner session in the UI, not "
                "just this chat.",
            )
        ]
    pref = await db.get(AgentPeerPreference, target.id)

    if call.capability_tag is None:
        if pref is not None:
            await db.delete(pref)
            await db.commit()
            await publish_tombstone(db, "agent_peer_preference", target.id)
        message = _system_message(
            db, rivulet, f"@{agent.name} cleared peer preference for agent @{target.name}."
        )
        return [message]

    if pref is None:
        pref = AgentPeerPreference(agent_id=target.id, capability_tag=call.capability_tag)
        db.add(pref)
    else:
        pref.capability_tag = call.capability_tag
        pref.vector_clock += 1
    await db.commit()
    await publish_current_state(db, "agent_peer_preference", target.id)

    message = _system_message(
        db,
        rivulet,
        f"@{agent.name} set peer preference for agent @{target.name} to {call.capability_tag!r}.",
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "agent_peer_preference_updated", "agent_id": target.id, "updated_by": agent.id},
    )
    return [message]


async def _handle_rollback_agent_version_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: RollbackAgentVersionCall
) -> list[Message]:
    """#190: an agent called the rollback_agent_version tool. Mirrors
    api/agents.py's rollback_agent_version handler (revert instructions/
    model to a prior version, record the rollback itself as a new
    version, regenerate routing rules when instructions changed).

    #310: also mirrors rollback_agent_version's agent_holds_owner_scope
    gate -- reverting a scoped agent's instructions/model is the same
    confused-deputy risk as editing them directly, and this trigger has
    no live session to check a claims.grant == "owner" bypass against, so
    it refuses outright."""
    result = await _resolve_agent_ref(db, call.agent_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to roll back agent {call.agent_ref!r}, but no agent with "
                "that id or name was found.",
            )
        ]
    target = result
    if await agent_holds_owner_scope(db, target.id):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to roll back agent @{target.name}, but it holds a "
                "capability scope -- that requires a live owner session in the UI, not just "
                "this chat.",
            )
        ]
    version_row = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == target.id, AgentVersion.version == call.version
        )
    )
    if version_row is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to roll back agent @{target.name} to version "
                f"{call.version}, but that version doesn't exist.",
            )
        ]

    instructions_changed = target.instructions != version_row.instructions
    target.instructions = version_row.instructions
    target.model = version_row.model
    await record_agent_version(db, target)
    target.vector_clock += 1
    await db.commit()

    if instructions_changed:
        await generate_and_store_routing_rules(db, target)

    await db.refresh(target)
    await register_agent_with_agentos(db, target)
    await publish_agent_change(db, target)

    message = _system_message(
        db, rivulet, f"@{agent.name} rolled back agent @{target.name} to version {call.version}."
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "agent_rolled_back", "agent_id": target.id, "version": call.version},
    )
    return [message]


async def _handle_list_agents_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#190: an agent called the (unscoped, read-only) list_agents
    tool."""
    agents = list((await db.scalars(select(Agent).order_by(Agent.name))).all())
    if not agents:
        content = f"@{agent.name} looked up the workspace's agents: there are none yet."
    else:
        lines = [f"@{agent.name} looked up the workspace's agents:"]
        for a in agents:
            lines.append(f"- @{a.name} (id: {a.id}, model: {a.model})")
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


async def _handle_create_team_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: CreateTeamCall
) -> list[Message]:
    """#190: an agent called the create_team tool. Mirrors api/teams.py's
    create_team handler -- Team.name carries no length or uniqueness
    constraint at either layer, so there's nothing to proactively
    validate beyond what TeamCreate itself would accept."""
    team = Team(name=call.name, description=call.description)
    db.add(team)
    await db.flush()
    await publish_current_state(db, "team", team.id)

    message = _system_message(
        db, rivulet, f"@{agent.name} created team {team.name!r} (id: {team.id})."
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "team_created", "team_id": team.id, "created_by": agent.id},
    )
    return [message]


async def _handle_update_team_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: UpdateTeamCall
) -> list[Message]:
    """#190: an agent called the update_team tool. Mirrors api/teams.py's
    update_team handler (full membership replace, ordered by position)
    -- except agent_ids entries are resolved by id-or-name
    (_resolve_agent_ref) rather than raw id only, since a model is far
    more likely to know a teammate's name than its uuid; api/teams.py's
    own TeamUpdate.agent_ids only ever accepts ids because a human's UI
    already resolved the name to an id before the request was built.

    #387: also mirrors that handler's #326/#353 membership gates --
    adding or dropping a scoped member is owner-only on the HTTP side,
    and this trigger has no live session to compare a claims.grant ==
    "owner" bypass against, so it refuses those diffs outright. Keeping
    or reordering an existing scoped member stays allowed, same as HTTP."""
    result = await _resolve_team_ref(db, call.team_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update team {call.team_ref!r}, but no team with that "
                "id or name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(t.id for t in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update team {call.team_ref!r}, but that name matches "
                f"{len(result)} teams ({ids}) -- specify by id instead.",
            )
        ]
    team = result

    if call.name is None and call.description is None and call.agent_ids is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update team {team.name!r}, but didn't specify any "
                "changes.",
            )
        ]

    resolved_agents: list[Agent] = []
    old_member_ids: set[str] | None = None
    new_member_ids: set[str] | None = None
    deduped: list[Agent] = []
    if call.agent_ids is not None:
        for ref in call.agent_ids:
            member = await _resolve_agent_ref(db, ref)
            if member is None:
                return [
                    _system_message(
                        db,
                        rivulet,
                        f"@{agent.name} tried to update team {team.name!r}, but {ref!r} "
                        "doesn't match any agent.",
                    )
                ]
            resolved_agents.append(member)
        # De-duped by id, first-seen order preserved -- two different refs
        # (e.g. an agent's name and its id) can resolve to the same Agent,
        # and TeamAgent's primary key is the (team_id, agent_id) pair, so
        # inserting the same agent twice would otherwise hit an
        # IntegrityError on commit.
        deduped = list({member.id: member for member in resolved_agents}.values())
        old_member_ids = set(
            (await db.scalars(select(TeamAgent.agent_id).where(TeamAgent.team_id == team.id))).all()
        )
        new_member_ids = {member.id for member in deduped}
        # #387 / #326: adding a scoped agent is the confused-deputy half
        # HTTP update_team already refuses without an owner session.
        # Checked before any field write so a refused membership change
        # can't piggy-back a name/description commit.
        for member in deduped:
            if member.id in old_member_ids:
                continue
            if await agent_holds_owner_scope(db, member.id):
                return [
                    _system_message(
                        db,
                        rivulet,
                        f"@{agent.name} tried to add @{member.name} to team {team.name!r}, but "
                        "it holds a capability scope -- that requires a live owner session in "
                        "the UI, not just this chat.",
                    )
                ]
        # #387 / #353: dropping a scoped member severs the @mention path
        # the owner set up, same standing as delete_team below.
        for member_id in old_member_ids - new_member_ids:
            if await agent_holds_owner_scope(db, member_id):
                dropped = await db.get(Agent, member_id)
                dropped_name = dropped.name if dropped is not None else member_id
                return [
                    _system_message(
                        db,
                        rivulet,
                        f"@{agent.name} tried to remove @{dropped_name} from team "
                        f"{team.name!r}, but it holds a capability scope -- that requires a "
                        "live owner session in the UI, not just this chat.",
                    )
                ]

    previous_name = team.name
    if call.name is not None:
        team.name = call.name
    if call.description is not None:
        team.description = call.description
    if call.agent_ids is not None:
        await db.execute(delete(TeamAgent).where(TeamAgent.team_id == team.id))
        for position, member in enumerate(deduped):
            db.add(TeamAgent(team_id=team.id, agent_id=member.id, position=position))

    await db.commit()
    await publish_current_state(db, "team", team.id)
    if old_member_ids is not None and new_member_ids is not None:
        await replace_join_entities(
            db,
            "team_agent",
            TEAM_AGENT_SPEC,
            {(team.id, member_id) for member_id in old_member_ids},
            {(team.id, member_id) for member_id in new_member_ids},
        )

    message = _system_message(db, rivulet, f"@{agent.name} updated team {previous_name!r}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "team_updated", "team_id": team.id, "updated_by": agent.id},
    )
    return [message]


async def _handle_delete_team_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, team_ref: str
) -> list[Message]:
    """#190: an agent called the delete_team tool. Mirrors api/teams.py's
    delete_team handler.

    #387: also mirrors that handler's #353 gate -- deleting a team that
    holds a scoped agent is owner-only on the HTTP side, and this trigger
    has no live session to check a claims.grant == "owner" bypass
    against, so it refuses outright."""
    result = await _resolve_team_ref(db, team_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete team {team_ref!r}, but no team with that id or "
                "name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(t.id for t in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete team {team_ref!r}, but that name matches "
                f"{len(result)} teams ({ids}) -- specify by id instead.",
            )
        ]
    team = result
    if await team_holds_owner_scoped_agent(db, team.id):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete team {team.name!r}, but it holds an agent with "
                "a capability scope -- that requires a live owner session in the UI, not just "
                "this chat.",
            )
        ]
    team_name = team.name
    team_id = team.id
    await db.delete(team)
    await db.commit()
    # #287: mirrors api/teams.py's delete_team -- same #238 failure mode.
    await publish_tombstone(db, "team", team_id)

    message = _system_message(db, rivulet, f"@{agent.name} deleted team {team_name!r}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "team_deleted", "team_name": team_name, "deleted_by": agent.id},
    )
    return [message]


async def _handle_list_teams_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#190: an agent called the (unscoped, read-only) list_teams
    tool."""
    teams = list((await db.scalars(select(Team).order_by(Team.name))).all())
    if not teams:
        content = f"@{agent.name} looked up the workspace's teams: there are none yet."
    else:
        lines = [f"@{agent.name} looked up the workspace's teams:"]
        for t in teams:
            member_ids = list(
                (
                    await db.scalars(
                        select(TeamAgent.agent_id)
                        .where(TeamAgent.team_id == t.id)
                        .order_by(TeamAgent.position)
                    )
                ).all()
            )
            names: list[str] = []
            for member_id in member_ids:
                member = await db.get(Agent, member_id)
                if member is not None:
                    names.append(f"@{member.name}")
            members = ", ".join(names) if names else "no members"
            lines.append(f"- {t.name!r} (id: {t.id}): {members}")
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


async def _resolve_mcp_server_ref(
    db: AsyncSession, server_ref: str
) -> MCPServer | list[MCPServer] | None:
    """Resolves an MCP server tool's `server` arg, which may be the
    server's id or its name. An id match is checked first and is always
    unambiguous. MCPServer.name carries no uniqueness constraint (db/
    models.py's MCPServer), same as Team.name, so a name match can hit
    more than one server -- returned as a list so callers can report that
    ambiguity the same way _resolve_team_ref does."""
    server = await db.get(MCPServer, server_ref)
    if server is not None:
        return server
    matches = list((await db.scalars(select(MCPServer).where(MCPServer.name == server_ref))).all())
    if len(matches) == 1:
        return matches[0]
    if matches:
        return matches
    return None


async def _sync_mcp_server_tools(db: AsyncSession, server: MCPServer) -> None:
    """(Re)discover `server`'s tools and replace its Tool rows with the
    current set -- used by both register_mcp_server and
    reconnect_mcp_server below (#191). Mirrors api/mcp_servers.py's
    private _connect_and_sync_tools; duplicated rather than shared since
    dispatch/service.py never imports from api/ (agentos/agent_lifecycle.py
    is the shared-logic exception, extracted because create_agent's
    registration is too stateful to safely duplicate -- this is a much
    smaller "discover + replace Tool rows" step). Doesn't commit --
    callers own the transaction."""
    await db.execute(delete(Tool).where(Tool.mcp_server_id == server.id))
    try:
        if server.transport == "stdio":
            assert server.command is not None
            args: list[str] = json.loads(server.args_json) if server.args_json else []
            discovered = await discover_tools(
                command=server.command, args=args, env=get_server_env(server)
            )
        else:
            assert server.url is not None
            discovered = await discover_tools(server.url, headers=get_server_headers(server))
    except MCPConnectionError:
        logger.warning(
            "Could not connect to MCP server %r (transport=%s) -- url=%s command=%s",
            server.name,
            server.transport,
            server.url,
            server.command,
            exc_info=True,
        )
        server.connected = False
        return

    server.connected = True
    server.last_connected_at = utcnow_iso()
    for discovered_tool in discovered:
        db.add(
            Tool(
                name=discovered_tool.name,
                description=discovered_tool.description,
                tool_type="mcp",
                mcp_server_id=server.id,
                mcp_tool_name=discovered_tool.name,
                mcp_input_schema_json=json.dumps(discovered_tool.input_schema),
            )
        )


def _mcp_server_requires_owner_to_mutate(server: MCPServer) -> bool:
    """#285: mirrors api/mcp_servers.py's private _requires_owner_to_mutate
    (duplicated rather than shared -- _sync_mcp_server_tools's docstring
    above explains why dispatch/service.py never imports from api/). A
    stdio server spawns a local subprocess, and a server with stored
    headers/env holds keychain secrets; the HTTP route requires a live
    owner *session* before reconnecting or deleting either. This trigger
    handler has no session to check -- whoever is chatting with `agent`
    drives it, which could be any invite-grant participant in the rivulet
    -- so there's no live grant to compare against api/mcp_servers.py's
    claims.grant. Refusing outright (never a "the owner is doing this"
    exception) is the only sound default here."""
    return (
        server.transport == "stdio" or bool(server.header_names_json) or bool(server.env_names_json)
    )


async def _handle_register_mcp_server_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: RegisterMcpServerCall
) -> list[Message]:
    """#191: an agent called the register_mcp_server tool. Only the
    streamable-http, headerless case this tool accepts (tools/builtin/
    mcp_servers.py's module docstring) -- registration always persists
    the row even if the connection attempt fails (NFR-2.4), matching api/
    mcp_servers.py's register_mcp_server handler; reconnect_mcp_server can
    retry later.

    #285: `call.url` came out of a completed model run, i.e. untrusted
    input by definition (the model could have been steered by injected
    rivulet content, same threat model security/network.py's module
    docstring describes for http_request.py) -- there's no "owner
    deliberately chose this" exception the way there is for an owner
    session driving api/mcp_servers.py's register_mcp_server directly, so
    every call here goes through check_host_is_public, unconditionally."""
    if not call.url.strip():
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to register MCP server {call.name!r}, but didn't provide "
                "a url.",
            )
        ]

    host = urlsplit(call.url).hostname
    if host is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to register MCP server {call.name!r}, but its url "
                f"{call.url!r} has no host to connect to.",
            )
        ]
    try:
        check_host_is_public(host)
    except BlockedHostError:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to register MCP server {call.name!r}, but its url points "
                "at an internal/private network address, which isn't permitted.",
            )
        ]

    server = MCPServer(name=call.name, transport="streamable-http", url=call.url)
    db.add(server)
    await db.flush()  # populates server.id (uuid7 default), needed to tag discovered Tool rows
    await _sync_mcp_server_tools(db, server)
    await db.commit()
    await publish_current_state(db, "mcp_server", server.id)

    if server.connected:
        message = _system_message(
            db,
            rivulet,
            f"@{agent.name} registered MCP server {server.name!r} (id: {server.id}) -- connected.",
        )
    else:
        message = _system_message(
            db,
            rivulet,
            f"@{agent.name} registered MCP server {server.name!r} (id: {server.id}), but "
            "couldn't connect to it -- use reconnect_mcp_server to retry.",
        )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "mcp_server_registered", "mcp_server_id": server.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_reconnect_mcp_server_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, server_ref: str
) -> list[Message]:
    """#191: an agent called the reconnect_mcp_server tool. Works for
    either transport and reuses whatever auth headers/env vars are
    already stored -- reconnecting doesn't enter a new secret, so it
    isn't subject to the same restriction register_mcp_server is (tools/
    builtin/mcp_servers.py's module docstring). Mirrors api/
    mcp_servers.py's reconnect_mcp_server handler.

    #285: api/mcp_servers.py's reconnect_mcp_server route now requires a
    live owner session for a stdio server, or one with stored headers/env
    (_requires_owner_to_mutate) -- this trigger has no live session to
    check (_mcp_server_requires_owner_to_mutate's docstring), so it
    refuses those outright rather than silently reusing stored secrets/
    respawning a subprocess on any rivulet participant's say-so.

    #365: the stored url is also re-run through check_host_is_public
    before every reconnect, unconditionally -- register-time checking
    alone is defeated by re-pointing the hostname's DNS at a private
    address afterwards, and (as with register above) there's no live
    owner session behind an agent-driven call to exempt."""
    result = await _resolve_mcp_server_ref(db, server_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to reconnect MCP server {server_ref!r}, but no server with "
                "that id or name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(s.id for s in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to reconnect MCP server {server_ref!r}, but that name "
                f"matches {len(result)} servers ({ids}) -- specify by id instead.",
            )
        ]
    server = result
    if _mcp_server_requires_owner_to_mutate(server):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to reconnect MCP server {server.name!r}, but it's a stdio "
                "server or has stored auth -- that requires a live owner session in the UI, not "
                "just this chat.",
            )
        ]

    # #365: re-run the register-time SSRF check on the *stored* url before
    # dialing it again -- DNS for the name can have been re-pointed at
    # loopback/LAN since registration, and like register above there's no
    # live session behind this call to grant an owner exception, so every
    # reconnect re-resolves and re-checks unconditionally. `server` is a
    # headerless streamable-http row here (the stdio case was refused just
    # above), so it always carries a url.
    assert server.url is not None
    host = urlsplit(server.url).hostname
    if host is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to reconnect MCP server {server.name!r}, but its stored "
                f"url {server.url!r} has no host to connect to.",
            )
        ]
    try:
        check_host_is_public(host)
    except BlockedHostError:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to reconnect MCP server {server.name!r}, but its stored "
                "url points at an internal/private network address, which isn't permitted.",
            )
        ]

    await _sync_mcp_server_tools(db, server)
    await db.commit()

    if server.connected:
        message = _system_message(
            db, rivulet, f"@{agent.name} reconnected MCP server {server.name!r}."
        )
    else:
        message = _system_message(
            db,
            rivulet,
            f"@{agent.name} tried to reconnect MCP server {server.name!r}, but couldn't connect "
            "to it.",
        )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "mcp_server_reconnected", "mcp_server_id": server.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_delete_mcp_server_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, server_ref: str
) -> list[Message]:
    """#191: an agent called the delete_mcp_server tool. Mirrors api/
    mcp_servers.py's unregister_mcp_server handler (stored secret
    cleanup, Tool rows cleared first for the FK).

    #285: api/mcp_servers.py's unregister_mcp_server route now requires a
    live owner session for a stdio server, or one with stored headers/env
    (_requires_owner_to_mutate) -- same "no live session to check" gap as
    reconnect above, closed the same way."""
    result = await _resolve_mcp_server_ref(db, server_ref)
    if result is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete MCP server {server_ref!r}, but no server with "
                "that id or name was found.",
            )
        ]
    if isinstance(result, list):
        ids = ", ".join(s.id for s in result)
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete MCP server {server_ref!r}, but that name matches "
                f"{len(result)} servers ({ids}) -- specify by id instead.",
            )
        ]
    server = result
    if _mcp_server_requires_owner_to_mutate(server):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete MCP server {server.name!r}, but it's a stdio "
                "server or has stored auth -- that requires a live owner session in the UI, not "
                "just this chat.",
            )
        ]
    server_name = server.name
    if server.header_names_json:
        delete_secret(mcp_header_ref(server.id))
    if server.env_names_json:
        delete_secret(mcp_env_ref(server.id))
    await db.execute(delete(Tool).where(Tool.mcp_server_id == server.id))
    await db.delete(server)
    await db.commit()
    # #287: mirrors api/mcp_servers.py's unregister_mcp_server -- same #238
    # failure mode.
    await publish_tombstone(db, "mcp_server", server.id)

    message = _system_message(db, rivulet, f"@{agent.name} deleted MCP server {server_name!r}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "mcp_server_deleted", "mcp_server_name": server_name, "deleted_by": agent.id},
    )
    return [message]


async def _handle_list_mcp_servers_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#191: an agent called the (unscoped, read-only) list_mcp_servers
    tool."""
    servers = list((await db.scalars(select(MCPServer).order_by(MCPServer.name))).all())
    if not servers:
        content = f"@{agent.name} looked up the workspace's MCP servers: there are none yet."
    else:
        lines = [f"@{agent.name} looked up the workspace's MCP servers:"]
        for s in servers:
            status = "connected" if s.connected else "not connected"
            target = s.url if s.transport == "streamable-http" else s.command
            lines.append(f"- {s.name!r} (id: {s.id}, {s.transport}, {status}): {target}")
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


# #192: workflow-level definition CRUD. Same source of truth as
# api/workflows.py's WorkflowCreate/WorkflowUpdate: the name pattern is
# copied here (rather than imported) to avoid api/workflows.py importing
# back into dispatch/service.py -- api -> dispatch is the existing
# dependency direction everywhere else in this file (e.g. _CHANNEL_NAME_MIN_LEN/
# _AGENT_NAME_MIN_LEN above are likewise each their own copy of the
# corresponding api/*.py constraint, not an import of it).
_WORKFLOW_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


async def _resolve_workflow_ref(db: AsyncSession, workflow_ref: str) -> Workflow | None:
    """Resolves a workflow tool's `workflow` arg, which may be the
    workflow's id or its name. An id match is checked first. Like
    Agent.name, Workflow.name is globally unique (db/models.py's
    idx_workflow_name), so there's no list-of-matches case to handle here,
    unlike _resolve_channel_ref/_resolve_team_ref."""
    workflow = await db.get(Workflow, workflow_ref)
    if workflow is not None:
        return workflow
    return await db.scalar(select(Workflow).where(Workflow.name == workflow_ref))


async def _handle_create_workflow_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: CreateWorkflowCall
) -> list[Message]:
    """#192: an agent called the create_workflow tool. Validation mirrors
    api/workflows.py's create_workflow handler (name pattern via
    _WORKFLOW_NAME_PATTERN, name uniqueness via idx_workflow_name) so a
    request that would 422/409 through the API is rejected here as a
    visible message instead, the same defensive posture
    _handle_create_channel_trigger already takes. The new workflow starts
    unpublished with no nodes/connections -- node/connection authoring
    isn't part of this tool set (tools/builtin/workflows.py's module
    docstring)."""
    if not _WORKFLOW_NAME_PATTERN.match(call.name):
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create workflow {call.name!r}, but workflow names must "
                "be 2-64 characters, lowercase letters/digits/hyphens only, starting with a "
                "letter.",
            )
        ]
    existing = await db.scalar(select(Workflow).where(Workflow.name == call.name))
    if existing is not None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to create workflow {call.name!r}, but a workflow with that "
                "name already exists.",
            )
        ]

    workflow = Workflow(name=call.name, description=call.description)
    db.add(workflow)
    await db.flush()  # populates workflow.id (uuid7 default), needed below
    await publish_current_state(db, "workflow", workflow.id)
    await db.commit()

    message = _system_message(
        db, rivulet, f"@{agent.name} created workflow {workflow.name!r} (id: {workflow.id})."
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "workflow_created", "workflow_id": workflow.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_update_workflow_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: UpdateWorkflowCall
) -> list[Message]:
    """#192: an agent called the update_workflow tool. Mirrors api/
    workflows.py's update_workflow handler for the name/description
    fields only -- on_failure_workflow_id/on_call_agent_id aren't
    settable through this tool (tools/builtin/workflows.py's module
    docstring).

    #387: also mirrors that handler's #356 published-name gate -- a
    published workflow's name *is* its `/{name}` trigger surface, and
    this trigger has no live session to grant an owner exception, so a
    rename is refused outright. Draft renames and description edits
    (published or not) stay open, same as HTTP."""
    workflow = await _resolve_workflow_ref(db, call.workflow_ref)
    if workflow is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update workflow {call.workflow_ref!r}, but no workflow "
                "with that id or name was found.",
            )
        ]

    if call.name is None and call.description is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update workflow {workflow.name!r}, but didn't specify "
                "any changes.",
            )
        ]

    previous_name = workflow.name
    if call.name is not None and call.name != workflow.name:
        # #387 / #356: renaming a published workflow detaches `/{name}`.
        if workflow.published:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to rename published workflow {workflow.name!r} -- "
                    "that requires a live owner session in the UI, not just this chat. "
                    "Unpublish it first.",
                )
            ]
        if not _WORKFLOW_NAME_PATTERN.match(call.name):
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to rename workflow {workflow.name!r}, but workflow "
                    "names must be 2-64 characters, lowercase letters/digits/hyphens only, "
                    "starting with a letter.",
                )
            ]
        conflict = await db.scalar(select(Workflow).where(Workflow.name == call.name))
        if conflict is not None:
            return [
                _system_message(
                    db,
                    rivulet,
                    f"@{agent.name} tried to rename workflow {workflow.name!r} to {call.name!r}, "
                    "but a workflow with that name already exists.",
                )
            ]
        workflow.name = call.name

    if call.description is not None:
        workflow.description = call.description

    await db.commit()
    await publish_current_state(db, "workflow", workflow.id)

    message = _system_message(db, rivulet, f"@{agent.name} updated workflow {previous_name!r}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "workflow_updated", "workflow_id": workflow.id, "agent_id": agent.id},
    )
    return [message]


async def _handle_delete_workflow_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, workflow_ref: str
) -> list[Message]:
    """#192: an agent called the delete_workflow tool. Mirrors api/
    workflows.py's delete_workflow handler (cascade delete of the
    workflow's own nodes/connections, Workflow.nodes/connections'
    cascade="all, delete-orphan").

    #387: that HTTP route is OwnerGrant. This trigger has no live
    session to check a claims.grant == "owner" bypass against, so it
    refuses outright rather than letting a guest @mention a
    workflows:manage agent into deleting the definition."""
    workflow = await _resolve_workflow_ref(db, workflow_ref)
    if workflow is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to delete workflow {workflow_ref!r}, but no workflow with "
                "that id or name was found.",
            )
        ]
    return [
        _system_message(
            db,
            rivulet,
            f"@{agent.name} tried to delete workflow {workflow.name!r} -- that requires a "
            "live owner session in the UI, not just this chat.",
        )
    ]


async def _handle_publish_workflow_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, workflow_ref: str
) -> list[Message]:
    """#192: an agent called the publish_workflow tool. Mirrors api/
    workflows.py's publish_workflow handler (refuses without an entry
    connection, the same "can this even run" check the engine itself
    makes at trigger time).

    #387: that HTTP route is OwnerGrant. This trigger has no live
    session to check a claims.grant == "owner" bypass against, so a
    successful publish (flipping the live `/{name}` surface on) is
    refused outright."""
    workflow = await _resolve_workflow_ref(db, workflow_ref)
    if workflow is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to publish workflow {workflow_ref!r}, but no workflow with "
                "that id or name was found.",
            )
        ]
    if workflow.published:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to publish workflow {workflow.name!r}, but it's already "
                "published.",
            )
        ]
    entry = await db.scalar(
        select(WorkflowConnection).where(
            WorkflowConnection.workflow_id == workflow.id,
            WorkflowConnection.from_node_id.is_(None),
        )
    )
    if entry is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to publish workflow {workflow.name!r}, but it has no entry "
                "point yet -- connect a first step before publishing.",
            )
        ]

    # #387: HTTP publish_workflow is OwnerGrant -- flipping `published`
    # attaches `/{name}` for every peer. No live session here.
    return [
        _system_message(
            db,
            rivulet,
            f"@{agent.name} tried to publish workflow {workflow.name!r} -- that requires a "
            "live owner session in the UI, not just this chat.",
        )
    ]


async def _handle_unpublish_workflow_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, workflow_ref: str
) -> list[Message]:
    """#192: an agent called the unpublish_workflow tool. Mirrors api/
    workflows.py's unpublish_workflow handler (reverts to draft; has no
    effect on a run already in flight).

    #387: that HTTP route is OwnerGrant. Detaching `/{name}` is the
    same live-surface rewrite as publish; no live session here, so
    refused outright."""
    workflow = await _resolve_workflow_ref(db, workflow_ref)
    if workflow is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to unpublish workflow {workflow_ref!r}, but no workflow "
                "with that id or name was found.",
            )
        ]
    if not workflow.published:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to unpublish workflow {workflow.name!r}, but it isn't "
                "published.",
            )
        ]

    # #387: HTTP unpublish_workflow is OwnerGrant -- same live-surface
    # rewrite as publish, no live session to grant an owner exception.
    return [
        _system_message(
            db,
            rivulet,
            f"@{agent.name} tried to unpublish workflow {workflow.name!r} -- that requires a "
            "live owner session in the UI, not just this chat.",
        )
    ]


async def _handle_list_workflows_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#192: an agent called the (unscoped, read-only) list_workflows
    tool."""
    workflows = list((await db.scalars(select(Workflow).order_by(Workflow.name))).all())
    if not workflows:
        content = f"@{agent.name} looked up the workspace's workflows: there are none yet."
    else:
        lines = [f"@{agent.name} looked up the workspace's workflows:"]
        for w in workflows:
            status = "published" if w.published else "draft"
            lines.append(f"- {w.name!r} (id: {w.id}, {status})")
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


# #193: mirrors api/settings.py's _DEFAULTS/_NOT_SYNCED_KEYS -- duplicated
# rather than imported, the same "small validation constant lives in both
# layers" split _WORKFLOW_NAME_PATTERN above already takes with api/
# workflows.py's _NAME_PATTERN, so dispatch/ never has to import from
# api/ (api/ already imports from dispatch/ in the other direction, e.g.
# api/rivulets.py -> dispatch_and_respond -- a reverse import here would
# risk a cycle).
_SETTINGS_DEFAULTS: dict[str, object] = {
    "dispatcher.model_override": None,
    "dispatcher.fallback_enabled": True,
    "model_tiers.override": None,
    "guard.turn_limit": 10,
    "guard.cycle_window": 8,
    "guard.cycle_threshold": 3,
    "guard.timeout_minutes": 30,
    "rivulet.summarization_enabled": True,
    "rivulet.context_threshold_pct": 80,
    "rivulet.recent_messages_kept": 20,
    "sync.eager_files_lan": True,
    "sync.eager_files_wan": False,
    "ui.port": 8484,
    "workflows.default_on_call_agent_id": None,
}

_NOT_SYNCED_SETTINGS_KEYS = frozenset({"ui.port"})


async def _handle_get_workspace_settings_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#193: an agent called the get_workspace_settings tool. Mirrors
    api/settings.py's get_settings_values handler -- every known setting,
    defaults merged with whatever's actually been stored. Unlike
    list_workflows/list_channels/etc, this tool carries a required_scope
    (tool_scopes.py's BUILTIN_TOOL_SCOPES) since the underlying route is
    OwnerGrant-gated for reads too, not just writes."""
    result = await db.execute(select(WorkspaceSetting))
    stored = {row.key: json.loads(row.value) for row in result.scalars().all()}
    current = {**_SETTINGS_DEFAULTS, **stored}

    lines = [f"@{agent.name} looked up the workspace's settings:"]
    for key in sorted(current):
        lines.append(f"- {key}: {current[key]!r}")
    content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


async def _handle_update_workspace_settings_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, settings: dict[str, object]
) -> list[Message]:
    """#193: an agent called the update_workspace_settings tool. Mirrors
    api/settings.py's patch_settings handler (unknown key rejects the
    whole update; existing rows get a vector_clock bump; every changed
    key except ui.port gets published for sync) plus an empty-dict guard
    Pydantic's own model_dump() doesn't need but a model-generated call
    does, the same "loosely parsed, meaningfully validated here" split
    _handle_update_agent_routing_rules_trigger already takes."""
    if not settings:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update workspace settings, but didn't specify any "
                "changes.",
            )
        ]
    unknown = sorted(key for key in settings if key not in _SETTINGS_DEFAULTS)
    if unknown:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to update workspace settings, but "
                f"{', '.join(repr(key) for key in unknown)} "
                f"{'is' if len(unknown) == 1 else 'are'} not a known setting.",
            )
        ]

    for key, value in settings.items():
        row = await db.get(WorkspaceSetting, key)
        if row is None:
            row = WorkspaceSetting(key=key, value=json.dumps(value))
            db.add(row)
        else:
            row.value = json.dumps(value)
            row.vector_clock += 1
    await db.commit()
    for key in settings:
        if key not in _NOT_SYNCED_SETTINGS_KEYS:
            await publish_current_state(db, "workspace_setting", key)

    message = _system_message(
        db,
        rivulet,
        f"@{agent.name} updated workspace settings: {', '.join(sorted(settings))}.",
    )
    publish(
        rivulet.id,
        "system_alert",
        {"type": "workspace_settings_updated", "keys": sorted(settings), "agent_id": agent.id},
    )
    return [message]


async def _handle_create_invite_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, call: CreateInviteCall
) -> list[Message]:
    """#193: an agent called the create_invite tool. Mirrors api/
    invites.py's create_invite handler, except the invite URL's host
    comes from a best-effort LAN address lookup (security/network.py's
    detect_lan_address) rather than the owner's own browser request --
    there's no live HTTP request to read a Host header from here, unlike
    the API route. Falls back to loopback (with a warning that the link
    only works from this machine) when no LAN address can be detected --
    the same fallback api/invites.py's lan_url logic reaches for, just
    without a request to compare against first.

    #241: the raw secret must never land in the returned Message -- a
    Message is a persisted, gossipsub-synced entity (sync/engine.py's
    FR-9.6), unlike the Invite row itself (db/models.py's Invite
    docstring: deliberately excluded from sync). Putting the secret in
    chat content would replicate it to every peer and every future
    context window that reads this rivulet's history, defeating the
    "shown once" property invites are built around (api/invites.py's
    module docstring) -- exactly what api/invites.py's own create_invite
    response avoids by returning the secret directly to the caller and
    never persisting it. The chat message here confirms only the invite
    id/display hint/expiry; the one-shot url rides the in-process SSE
    `system_alert` payload instead (streaming.py's publish() -- in-memory,
    per-process, never persisted or gossiped, the same "nowhere but this
    one delivery" shape the tool's own return value already has, see
    tools/builtin/invites.py's module docstring).

    #286: that `system_alert` still isn't safe to fan out to every
    subscriber of this rivulet's stream -- an invite-grant session can
    open the same stream an owner session can (api/rivulets.py's
    stream_rivulet has no OwnerGrant gate, by design), so an invitee
    sitting on an open EventSource would otherwise harvest the next
    owner-class invite URL the moment any agent here calls create_invite.
    publish()'s `owner_only=True` makes streaming.py skip every
    non-owner subscriber for this event specifically."""
    secret = keys.generate_invite_secret()
    expires_at = datetime.now(UTC) + timedelta(hours=call.expires_in_hours)
    invite = Invite(
        secret_hash=keys.hash_invite_secret(secret),
        display_name_hint=call.display_name_hint,
        max_uses=call.max_uses,
        expires_at=expires_at.isoformat(),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    lan_ip = detect_lan_address()
    port = get_settings().app_server_port
    url = f"http://{lan_ip or '127.0.0.1'}:{port}/invite/{invite.id}.{secret}"

    hint = f" for {call.display_name_hint!r}" if call.display_name_hint else ""
    lines = [
        f"@{agent.name} created a workspace invite (id: {invite.id}){hint}, "
        f"expires {invite.expires_at}."
    ]
    if lan_ip is None:
        lines.append(
            "Couldn't detect a LAN address, so this link only works from this machine -- "
            "share the workspace's real network address manually if the invitee is remote."
        )
    lines.append(
        "The link itself was pushed live to this rivulet just now, shown only that once -- "
        "if it was missed, revoke this invite and create a new one."
    )
    content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    publish(
        rivulet.id,
        "system_alert",
        {
            "type": "invite_created",
            "invite_id": invite.id,
            "agent_id": agent.id,
            "url": url,
            "loopback_only": lan_ip is None,
        },
        owner_only=True,
    )
    return [message]


async def _handle_list_invites_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent
) -> list[Message]:
    """#193: an agent called the list_invites tool. Mirrors api/
    invites.py's list_invites handler -- never surfaces a secret, only
    ever stored as a bcrypt hash to begin with. Unlike list_workflows/
    list_channels/etc, this tool carries a required_scope (tool_scopes.py's
    BUILTIN_TOOL_SCOPES) since the underlying route is OwnerGrant-gated
    for reads too, not just writes."""
    result = await db.execute(select(Invite).order_by(Invite.created_at.desc()))
    invites = list(result.scalars().all())
    if not invites:
        content = f"@{agent.name} looked up the workspace's invites: there are none yet."
    else:
        lines = [f"@{agent.name} looked up the workspace's invites:"]
        for invite in invites:
            status = "revoked" if invite.revoked else "active"
            hint = f" for {invite.display_name_hint!r}" if invite.display_name_hint else ""
            lines.append(
                f"- {invite.id}{hint}: {invite.use_count}/{invite.max_uses} used, "
                f"expires {invite.expires_at}, {status}"
            )
        content = "\n".join(lines)

    message = _system_message(db, rivulet, content)
    return [message]


async def _handle_revoke_invite_trigger(
    db: AsyncSession, rivulet: Rivulet, agent: Agent, invite_id: str
) -> list[Message]:
    """#193: an agent called the revoke_invite tool. Mirrors api/
    invites.py's revoke_invite handler exactly -- including its
    idempotence (revoking an already-revoked invite still succeeds, no
    special-cased rejection)."""
    invite = await db.get(Invite, invite_id)
    if invite is None:
        return [
            _system_message(
                db,
                rivulet,
                f"@{agent.name} tried to revoke invite {invite_id!r}, but no invite with that id "
                "was found.",
            )
        ]

    invite.revoked = True
    await db.commit()

    message = _system_message(db, rivulet, f"@{agent.name} revoked invite {invite_id!r}.")
    publish(
        rivulet.id,
        "system_alert",
        {"type": "invite_revoked", "invite_id": invite_id, "agent_id": agent.id},
    )
    return [message]
