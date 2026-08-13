"""#96: workflows/engine.py's run-tracing instrumentation, exercised
directly against a DB session (mirrors test_workflow_engine.py's own
style). Covers: a linear run's workflow_run/workflow_node_run span tree,
the previously-missing AgentRun row for a workflow 'agent' node (see
workflows/nodes.py's execute_agent_node docstring), a nested 'workflow'
node's child run nesting under its own node's span, and a paused/resumed
run's span being closed out on resume without gaining new spans for the
post-resume portion (resume_workflow's own docstring)."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import (
    Agent,
    AgentRun,
    Channel,
    Rivulet,
    RunSpan,
    RunTrace,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
)
from rivulets.tracing import finish_trace, start_trace
from rivulets.workflows.engine import resume_workflow, run_workflow

# Small subset of test_workflow_engine.py's own fixtures, duplicated here
# rather than imported cross-module (no other test file in this suite
# imports from another) -- just enough to build the linear/agent/nested
# workflow shapes these tracing tests need.


async def _make_rivulet(db: AsyncSession) -> Rivulet:
    channel = Channel(name="wf-tracing-test")
    db.add(channel)
    await db.flush()
    rivulet = Rivulet(channel_id=channel.id, created_by="human")
    db.add(rivulet)
    await db.flush()
    return rivulet


async def _make_workflow(db: AsyncSession, name: str = "greet") -> Workflow:
    workflow = Workflow(name=name)
    db.add(workflow)
    await db.flush()
    return workflow


def _transform_node(workflow_id: str, name: str, template: str) -> WorkflowNode:
    return WorkflowNode(
        workflow_id=workflow_id,
        name=name,
        node_type="transform",
        config_json=json.dumps({"template": template}),
    )


def _workflow_node(workflow_id: str, name: str, child_workflow_id: str) -> WorkflowNode:
    return WorkflowNode(
        workflow_id=workflow_id,
        name=name,
        node_type="workflow",
        child_workflow_id=child_workflow_id,
    )


async def _entry_connect(db: AsyncSession, workflow_id: str, node_id: str) -> None:
    db.add(WorkflowConnection(workflow_id=workflow_id, from_node_id=None, to_node_id=node_id))


async def test_linear_run_produces_workflow_run_and_node_spans(db_session: AsyncSession) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="traced-flow")
    step_a = _transform_node(workflow.id, "step-a", "a: {input}")
    step_b = _transform_node(workflow.id, "step-b", "b: {input}")
    db_session.add_all([step_a, step_b])
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, step_a.id)
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=step_a.id, to_node_id=step_b.id)
    )
    await db_session.commit()

    trace_ctx = await start_trace(
        db_session, trigger_type="message", label="go", rivulet_id=rivulet.id, channel_id=None
    )
    run = await run_workflow(
        db_session,
        workflow,
        rivulet,
        "hello",
        triggered_by="human",
        triggered_by_id="h1",
        trace_ctx=trace_ctx,
    )
    await finish_trace(db_session, trace_ctx.trace_id)

    assert run.status == "completed"

    trace = await db_session.get(RunTrace, trace_ctx.trace_id)
    assert trace is not None
    assert trace.status == "completed"
    assert trace.span_count == 3  # workflow_run + 2 node spans

    spans = list(
        (
            await db_session.scalars(select(RunSpan).where(RunSpan.trace_id == trace_ctx.trace_id))
        ).all()
    )
    run_span = next(s for s in spans if s.span_type == "workflow_run")
    assert run_span.entity_id == run.id
    assert run_span.parent_span_id is None
    assert run_span.status == "completed"

    node_spans = sorted(
        (s for s in spans if s.span_type == "workflow_node_run"), key=lambda s: s.started_at
    )
    assert [s.name for s in node_spans] == ["step-a", "step-b"]
    assert all(s.parent_span_id == run_span.id for s in node_spans)
    assert all(s.status == "completed" for s in node_spans)


async def test_agent_node_records_agent_run_and_nests_its_span(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes the gap called out in workflows/nodes.py's execute_agent_node
    docstring: before #96, a workflow 'agent' node never produced an
    AgentRun row at all, so it was invisible to the usage dashboard (#28)
    and to any trace built from that data."""
    agent = Agent(
        name="Workflow Greeter",
        description="An agent invoked from inside a workflow, for tracing tests.",
        instructions="Say hi.",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="agent-traced-flow")
    node = WorkflowNode(workflow_id=workflow.id, name="greet", node_type="agent", agent_id=agent.id)
    db_session.add(node)
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, node.id)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(get_content_as_string=lambda: "Hello there!")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    assert (await db_session.scalars(select(AgentRun))).first() is None

    trace_ctx = await start_trace(
        db_session, trigger_type="message", label="go", rivulet_id=rivulet.id, channel_id=None
    )
    run = await run_workflow(
        db_session,
        workflow,
        rivulet,
        "hi",
        triggered_by="human",
        triggered_by_id="h1",
        trace_ctx=trace_ctx,
    )
    await finish_trace(db_session, trace_ctx.trace_id)

    assert run.status == "completed"

    agent_runs = list((await db_session.scalars(select(AgentRun))).all())
    assert len(agent_runs) == 1
    assert agent_runs[0].agent_id == agent.id
    assert agent_runs[0].status == "completed"

    spans = list(
        (
            await db_session.scalars(select(RunSpan).where(RunSpan.trace_id == trace_ctx.trace_id))
        ).all()
    )
    node_span = next(s for s in spans if s.span_type == "workflow_node_run")
    agent_span = next(s for s in spans if s.span_type == "agent_run")
    assert agent_span.parent_span_id == node_span.id
    assert agent_span.entity_id == agent_runs[0].id
    assert agent_span.name == "Workflow Greeter"


async def test_nested_workflow_node_span_nests_under_its_own_node(db_session: AsyncSession) -> None:
    rivulet = await _make_rivulet(db_session)

    child = await _make_workflow(db_session, name="child-traced")
    child.published = True
    child_step = _transform_node(child.id, "child-step", "child saw: {input}")
    db_session.add(child_step)
    await db_session.flush()
    await _entry_connect(db_session, child.id, child_step.id)

    parent = await _make_workflow(db_session, name="parent-traced")
    invoke = _workflow_node(parent.id, "invoke-child", child.id)
    db_session.add(invoke)
    await db_session.flush()
    await _entry_connect(db_session, parent.id, invoke.id)
    await db_session.commit()

    trace_ctx = await start_trace(
        db_session, trigger_type="message", label="go", rivulet_id=rivulet.id, channel_id=None
    )
    run = await run_workflow(
        db_session,
        parent,
        rivulet,
        "hello",
        triggered_by="human",
        triggered_by_id="h1",
        trace_ctx=trace_ctx,
    )
    await finish_trace(db_session, trace_ctx.trace_id)

    assert run.status == "completed"

    spans = list(
        (
            await db_session.scalars(select(RunSpan).where(RunSpan.trace_id == trace_ctx.trace_id))
        ).all()
    )
    parent_run_span = next(
        s for s in spans if s.span_type == "workflow_run" and s.entity_id == run.id
    )
    invoke_node_span = next(
        s for s in spans if s.span_type == "workflow_node_run" and s.name == "invoke-child"
    )
    assert invoke_node_span.parent_span_id == parent_run_span.id

    child_run_span = next(
        s for s in spans if s.span_type == "workflow_run" and s.entity_id != run.id
    )
    # Nests under the specific node that invoked it, not the parent run's
    # own root span -- see _execute_workflow_node's docstring.
    assert child_run_span.parent_span_id == invoke_node_span.id


async def test_untraced_run_workflow_creates_no_spans(db_session: AsyncSession) -> None:
    """trace_ctx defaults to None -- every existing (untraced) caller of
    run_workflow must keep working exactly as before #96."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="untraced-flow")
    step = _transform_node(workflow.id, "step", "{input}")
    db_session.add(step)
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, step.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    assert (await db_session.scalars(select(RunSpan))).first() is None
    assert (await db_session.scalars(select(RunTrace))).first() is None


async def test_paused_run_span_stays_open_until_resumed(db_session: AsyncSession) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="ask-traced")
    ask = WorkflowNode(workflow_id=workflow.id, name="ask", node_type="human_input")
    echo = _transform_node(workflow.id, "echo", "confirmed: {input}")
    db_session.add_all([ask, echo])
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, ask.id)
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=ask.id, to_node_id=echo.id)
    )
    await db_session.commit()

    trace_ctx = await start_trace(
        db_session, trigger_type="message", label="go", rivulet_id=rivulet.id, channel_id=None
    )
    run = await run_workflow(
        db_session,
        workflow,
        rivulet,
        "please confirm",
        triggered_by="human",
        triggered_by_id="h1",
        trace_ctx=trace_ctx,
    )
    assert run.status == "awaiting_human"

    run_span = await db_session.scalar(
        select(RunSpan).where(RunSpan.span_type == "workflow_run", RunSpan.entity_id == run.id)
    )
    assert run_span is not None
    assert run_span.status == "running"  # left open across the pause boundary
    assert run_span.completed_at is None

    resumed = await resume_workflow(db_session, run, "yes, confirmed")
    assert resumed.status == "completed"

    await db_session.refresh(run_span)
    assert run_span.status == "completed"
    assert run_span.completed_at is not None

    # #96's documented v1 limitation: nothing executed after the resume
    # (the "echo" step) gets a new span.
    node_spans = list(
        (
            await db_session.scalars(
                select(RunSpan).where(
                    RunSpan.trace_id == trace_ctx.trace_id,
                    RunSpan.span_type == "workflow_node_run",
                )
            )
        ).all()
    )
    assert [s.name for s in node_spans] == ["ask"]
    assert node_spans[0].status == "completed"
