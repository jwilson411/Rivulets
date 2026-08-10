"""#100: tool-call audit logging (agentos/tool_audit.py's `log_tool_calls`)
and the unattended sensitive-tool gate (`ensure_unattended_tools_allowed`,
wired into workflows/nodes.py's execute_agent_node via WorkflowRun.
unattended). Mirrors test_workflow_engine_tracing.py's own style: a fake
`run_agent` monkeypatched onto workflows.nodes, exercised through the real
run_workflow/engine.py call chain rather than calling execute_agent_node
directly, so the WorkflowRun.unattended derivation/inheritance is covered
too, not just the gate function in isolation."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from agno.metrics import ToolCallMetrics
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.tool_audit import (
    UnattendedSensitiveToolError,
    ensure_unattended_tools_allowed,
    log_tool_calls,
)
from rivulets.agentos.tool_resolution import seed_builtin_tools
from rivulets.db.models import (
    Agent,
    AgentRun,
    AgentTool,
    Channel,
    Rivulet,
    Tool,
    ToolCallLog,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
)
from rivulets.workflows.engine import run_workflow

_AGENT_FIELDS = {
    "description": "A test agent for tool audit tests.",
    "instructions": "Do the thing.",
    "model": "openai:gpt-4",
}


async def _make_agent(db: AsyncSession, **overrides: Any) -> Agent:
    fields = {**_AGENT_FIELDS, **overrides}
    agent = Agent(name=fields.pop("name", "Test Agent"), **fields)
    db.add(agent)
    await db.flush()
    return agent


async def _assign_sensitive_tool(
    db: AsyncSession, agent: Agent, tool_name: str = "execute_python"
) -> None:
    await seed_builtin_tools(db)
    tool_row = (await db.execute(select(Tool).where(Tool.name == tool_name))).scalar_one()
    assert tool_row.sensitive is True
    db.add(AgentTool(agent_id=agent.id, tool_id=tool_row.id))
    await db.commit()


async def _make_rivulet(db: AsyncSession) -> Rivulet:
    channel = Channel(name="tool-audit-test")
    db.add(channel)
    await db.flush()
    rivulet = Rivulet(channel_id=channel.id, created_by="human")
    db.add(rivulet)
    await db.flush()
    return rivulet


async def _make_workflow_with_agent_node(db: AsyncSession, agent: Agent, name: str) -> Workflow:
    workflow = Workflow(name=name)
    db.add(workflow)
    await db.flush()
    node = WorkflowNode(
        workflow_id=workflow.id, name="respond", node_type="agent", agent_id=agent.id
    )
    db.add(node)
    await db.flush()
    db.add(WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id))
    await db.commit()
    return workflow


# --- seed_builtin_tools sensitivity -----------------------------------------


async def test_seed_builtin_tools_marks_only_the_documented_sensitive_set(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    result = await db_session.execute(
        select(Tool.name, Tool.sensitive).where(Tool.tool_type == "builtin")
    )
    sensitivity = {name: sensitive for name, sensitive in result.all()}
    assert sensitivity["execute_python"] is True
    assert sensitivity["http_request"] is True
    assert sensitivity["write_file"] is True
    assert sensitivity["query_workspace_db"] is True
    # Read-only/low-risk builtins stay unmarked.
    assert sensitivity["read_file"] is False
    assert sensitivity["list_files"] is False
    assert sensitivity["web_search"] is False


async def test_seed_builtin_tools_backfills_sensitivity_on_existing_rows(
    db_session: AsyncSession,
) -> None:
    """An install that seeded these rows before #100 existed would have
    every builtin at sensitive=False forever without this -- seed_builtin_
    tools is the only place that ever revisits an existing row."""
    db_session.add(
        Tool(name="execute_python", description="old", tool_type="builtin", sensitive=False)
    )
    await db_session.commit()

    await seed_builtin_tools(db_session)

    tool_row = (
        await db_session.execute(select(Tool).where(Tool.name == "execute_python"))
    ).scalar_one()
    assert tool_row.sensitive is True


# --- ensure_unattended_tools_allowed ----------------------------------------


async def test_gate_allows_agent_with_no_sensitive_tools(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    await ensure_unattended_tools_allowed(db_session, agent)  # must not raise


async def test_gate_blocks_unapproved_agent_with_sensitive_tool(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    await _assign_sensitive_tool(db_session, agent)

    with pytest.raises(UnattendedSensitiveToolError, match="execute_python"):
        await ensure_unattended_tools_allowed(db_session, agent)


async def test_gate_allows_approved_agent_with_sensitive_tool(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session, approved_for_unattended_tools=True)
    await _assign_sensitive_tool(db_session, agent)

    await ensure_unattended_tools_allowed(db_session, agent)  # must not raise


# --- log_tool_calls ----------------------------------------------------------


async def test_log_tool_calls_records_one_row_per_call(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    run_output = RunOutput(
        content="done",
        tools=[
            ToolExecution(
                tool_name="execute_python",
                tool_args={"code": "print(1)"},
                tool_call_error=False,
                result="1",
                metrics=ToolCallMetrics(duration=0.25),
            ),
            ToolExecution(
                tool_name="read_file",
                tool_args={"path": "a.txt"},
                tool_call_error=True,
                result="not found",
                metrics=None,
            ),
        ],
    )
    run = await record_agent_run(db_session, agent, agent.model, None, "completed", run_output)
    await db_session.commit()

    logs = list(
        (
            await db_session.scalars(
                select(ToolCallLog)
                .where(ToolCallLog.agent_run_id == run.id)
                .order_by(ToolCallLog.created_at)
            )
        ).all()
    )
    assert len(logs) == 2

    exec_log, read_log = logs
    assert exec_log.tool_name == "execute_python"
    assert exec_log.sensitive is True
    assert exec_log.status == "success"
    assert exec_log.duration_ms == 250
    assert exec_log.arguments_json is not None
    assert json.loads(exec_log.arguments_json) == {"code": "print(1)"}

    assert read_log.tool_name == "read_file"
    assert read_log.sensitive is False
    assert read_log.status == "error"
    assert read_log.result_summary == "not found"


async def test_log_tool_calls_truncates_oversized_fields(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    huge_result = "x" * 5000
    run_output = RunOutput(
        content="done",
        tools=[ToolExecution(tool_name="read_file", tool_args={}, result=huge_result)],
    )
    run = await record_agent_run(db_session, agent, agent.model, None, "completed", run_output)
    await db_session.commit()

    log = (
        await db_session.scalars(select(ToolCallLog).where(ToolCallLog.agent_run_id == run.id))
    ).one()
    assert log.result_summary is not None
    assert len(log.result_summary) < 5000
    assert log.result_summary.endswith("…")


async def test_log_tool_calls_no_rows_when_run_made_no_tool_calls(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    run_output = RunOutput(content="just a reply, no tools")
    run = await record_agent_run(db_session, agent, agent.model, None, "completed", run_output)
    await db_session.commit()

    logs = await db_session.scalars(select(ToolCallLog).where(ToolCallLog.agent_run_id == run.id))
    assert logs.first() is None


async def test_log_tool_calls_tolerates_duck_typed_run_output(db_session: AsyncSession) -> None:
    """dispatch tests monkeypatch run_agent with a plain SimpleNamespace
    that has no `.tools` attribute at all (accounting.py's own
    `record_agent_run` docstring) -- log_tool_calls must be just as
    tolerant of that as the metrics lookup right above its call site."""
    agent = await _make_agent(db_session)
    run_output = SimpleNamespace(status="completed", get_content_as_string=lambda: "hi")
    await log_tool_calls(
        db_session,
        AgentRun(agent_id=agent.id, model=agent.model, status="completed"),
        run_output,  # pyright: ignore[reportArgumentType]
    )
    # No exception is the assertion -- nothing to flush/commit since no
    # tool calls were logged.


# --- end-to-end: unattended gate wired into workflow agent nodes ------------


async def test_unattended_scheduled_run_blocks_unapproved_sensitive_agent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session, name="Coder")
    await _assign_sensitive_tool(db_session, agent)
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow_with_agent_node(db_session, agent, "sensitive-flow")

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        pytest.fail("run_agent should never be called -- the gate must block before this")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session,
        workflow,
        rivulet,
        "go",
        triggered_by="schedule",
        triggered_by_id="sched-1",
    )

    assert run.unattended is True
    assert run.status == "failed"
    assert run.error_message is not None
    assert "Coder" in run.error_message
    assert "execute_python" in run.error_message


async def test_unattended_scheduled_run_allows_approved_sensitive_agent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session, name="Approved Coder", approved_for_unattended_tools=True)
    await _assign_sensitive_tool(db_session, agent)
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow_with_agent_node(db_session, agent, "approved-sensitive-flow")

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(get_content_as_string=lambda: "ran fine", tools=None)

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="schedule", triggered_by_id="sched-1"
    )

    assert run.unattended is True
    assert run.status == "completed"


async def test_human_triggered_run_is_not_gated_even_with_unapproved_sensitive_agent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate only ever applies to unattended (schedule/remediation)
    runs -- ordinary chat/slash-command use of a sensitive tool is
    unaffected, same as it was before #100."""
    agent = await _make_agent(db_session, name="Unapproved Coder")
    await _assign_sensitive_tool(db_session, agent)
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow_with_agent_node(db_session, agent, "human-triggered-flow")

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(get_content_as_string=lambda: "ran fine", tools=None)

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.unattended is False
    assert run.status == "completed"
