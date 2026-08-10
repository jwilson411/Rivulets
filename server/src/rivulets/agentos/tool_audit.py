"""Tool-call audit logging and the unattended sensitive-tool gate (#100).

Two independent pieces sharing a module because they're two sides of the
same problem ("what happens when a tool with real blast radius runs with
nobody watching"):

  - `log_tool_calls` records what actually happened -- called from
    `agentos/accounting.py`'s `record_agent_run`, the one choke point
    every `run_agent` caller already goes through, so both ordinary chat
    tool use and workflow agent-node tool use get audited uniformly.
  - `ensure_unattended_tools_allowed` decides whether a call should be
    allowed to happen at all -- called from `workflows/nodes.py`'s
    `execute_agent_node` *before* invoking the agent, only when that
    node's run is `unattended` (WorkflowRun.unattended's docstring).

Deliberately not touching agno's own `requires_confirmation`/`approval_id`
tool-confirmation machinery (agno.models.response.ToolExecution) -- that
would need a real confirm/deny UI for *every* tool call, attended or not,
which changes the ordinary chat experience this issue never asked to
change. This is a simpler pre-flight check: refuse to even start the run
when it's unattended and the agent isn't approved, leave attended runs
byte-for-byte as they were before this module existed.
"""

import json
import logging

from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.tool_resolution import SENSITIVE_BUILTIN_TOOL_NAMES
from rivulets.db.models import Agent, AgentRun, AgentTool, Tool, ToolCallLog

logger = logging.getLogger(__name__)

# Generous but bounded -- this is an audit trail for "what ran, with
# roughly what, and did it work", not a byte-exact replay log. A tool
# that returns megabytes of data (a large file read) shouldn't make
# tool_call_log unbounded.
_MAX_FIELD_CHARS = 2000


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= _MAX_FIELD_CHARS else value[:_MAX_FIELD_CHARS] + "…"


class UnattendedSensitiveToolError(Exception):
    """Raised by `ensure_unattended_tools_allowed` when an unattended run
    would invoke an agent that has a sensitive tool assigned and hasn't
    been approved for unattended use. Caught nowhere specially -- it
    propagates and fails the workflow node exactly like any other
    execute_agent_node exception (workflows/engine.py's
    `_run_node_with_retries`), which is already surfaced through the
    normal failed-run notification path (#94's on-call @mention, or
    surfacing in the workflow's run history)."""


async def ensure_unattended_tools_allowed(db: AsyncSession, agent: Agent) -> None:
    """No-op if `agent.approved_for_unattended_tools` is already set, or if
    it has no sensitive tool assigned -- so the common case (an agent with
    only read/summarize-style tools, or one already approved) costs one
    cheap query and nothing else. Only raises when both are true: a
    sensitive tool is assigned AND no one has approved this agent for
    unattended use of it yet."""
    if agent.approved_for_unattended_tools:
        return
    result = await db.execute(
        select(Tool.name)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent.id, Tool.sensitive.is_(True))
    )
    sensitive_names = sorted({row for row in result.scalars().all()})
    if not sensitive_names:
        return
    raise UnattendedSensitiveToolError(
        f"Agent {agent.name!r} has sensitive tool(s) assigned ({', '.join(sensitive_names)}) "
        "and hasn't been approved to run them unattended. Approve unattended tool use in this "
        "agent's settings before using it in a schedule, remediation workflow, or nested "
        "workflow reachable from one."
    )


async def log_tool_calls(db: AsyncSession, agent_run: AgentRun, run_output: RunOutput) -> None:
    """Persists one ToolCallLog row per tool call agno recorded on this
    run's terminal RunCompletedEvent (`run_output.tools` --
    agno.models.response.ToolExecution). `run_output.tools` is None/empty
    for a run that made no tool calls (most dispatcher-tier calls, and any
    agent turn that just replied in text) -- nothing to log, not an error.

    `sensitive` is looked up against SENSITIVE_BUILTIN_TOOL_NAMES rather
    than a DB join back to `Tool` -- builtin tool names are a fixed,
    unique set, so this avoids a query per tool call on the hot path every
    chat/workflow run already goes through. Custom/mcp tools always log
    `sensitive=False` for now (Tool.sensitive's own docstring: no UI to
    mark those sensitive yet, v1 scope is the fixed builtin set)."""
    # Explicit annotation, not just `run_output.tools` -- getattr (same
    # reasoning as record_agent_run's own `metrics` lookup just above this
    # call site) tolerates the test suite's SimpleNamespace run_agent
    # doubles that don't set `.tools` at all, while the annotation itself
    # (not inferred from getattr's Any-erased return) is what gives every
    # `call.<field>` access below its real agno-defined type instead of
    # Unknown.
    tool_calls: list[ToolExecution] = getattr(run_output, "tools", None) or []
    if not tool_calls:
        return
    for call in tool_calls:
        tool_name = call.tool_name or "unknown"
        duration_ms = None
        if call.metrics is not None and call.metrics.duration is not None:
            duration_ms = int(call.metrics.duration * 1000)
        db.add(
            ToolCallLog(
                agent_id=agent_run.agent_id,
                agent_run_id=agent_run.id,
                tool_name=tool_name,
                sensitive=tool_name in SENSITIVE_BUILTIN_TOOL_NAMES,
                status="error" if call.tool_call_error else "success",
                arguments_json=_truncate(json.dumps(call.tool_args) if call.tool_args else None),
                result_summary=_truncate(call.result),
                duration_ms=duration_ms,
            )
        )
    await db.flush()
