"""#24: the workflow execution engine (workflows/engine.py) and its
built-in node executors (workflows/nodes.py), exercised directly against
a DB session (db_session fixture) rather than through the HTTP layer —
mirrors test_rivulet_dispatch.py's split between engine-level and
API-level coverage. Deterministic node types (transform/conditional/merge)
need no monkeypatching; the one 'agent' node test monkeypatches
`rivulets.workflows.nodes.run_agent`, the same seam test_handoff.py uses
for `rivulets.dispatch.service.run_agent`.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import (
    Agent,
    AgentTool,
    AgentToolScope,
    Channel,
    Message,
    Rivulet,
    Tool,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowSchedule,
)
from rivulets.workflows.engine import (
    MAX_NODE_VISITS_PER_RUN,
    MAX_WORKFLOW_NESTING_DEPTH,
    resume_workflow,
    run_workflow,
)


async def _make_rivulet(db: AsyncSession) -> Rivulet:
    channel = Channel(name="wf-test")
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


def _transform_node(workflow_id: str, name: str, template: str, **kwargs: Any) -> WorkflowNode:
    return WorkflowNode(
        workflow_id=workflow_id,
        name=name,
        node_type="transform",
        config_json=json.dumps({"template": template}),
        **kwargs,
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


async def test_linear_transform_chain_completes_and_chains_output(
    db_session: AsyncSession,
) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session)
    upper = _transform_node(workflow.id, "shout", "{input}!!!")
    wrap = _transform_node(workflow.id, "wrap", "<<{input}>>")
    db_session.add_all([upper, wrap])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=upper.id),
            WorkflowConnection(workflow_id=workflow.id, from_node_id=upper.id, to_node_id=wrap.id),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="human-1"
    )

    assert run.status == "completed"
    assert run.error_message is None
    assert rivulet.agentos_session_id == rivulet.id

    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id).order_by(Message.created_at)
    )
    messages = list(result.scalars().all())
    # step(upper), output(upper), step(wrap), output(wrap)
    assert [m.content_type for m in messages] == ["workflow_step", "text", "workflow_step", "text"]
    assert messages[1].content == "hi!!!"
    assert messages[3].content == "<<hi!!!>>"


async def test_workflow_with_no_entry_point_fails_immediately(db_session: AsyncSession) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="empty")
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="human-1"
    )

    assert run.status == "failed"
    assert run.error_message == "Workflow has no entry point"
    result = await db_session.execute(select(Message).where(Message.rivulet_id == rivulet.id))
    messages = list(result.scalars().all())
    assert len(messages) == 1
    assert messages[0].content_type == "system_alert"


async def test_conditional_node_ends_run_early_when_predicate_fails(
    db_session: AsyncSession,
) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="gate")
    gate = WorkflowNode(
        workflow_id=workflow.id,
        name="gate",
        node_type="conditional",
        config_json=json.dumps({"contains": "urgent"}),
    )
    unreachable = _transform_node(workflow.id, "unreachable", "should not run: {input}")
    db_session.add_all([gate, unreachable])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=gate.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=gate.id, to_node_id=unreachable.id
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session,
        workflow,
        rivulet,
        "just a normal message",
        triggered_by="human",
        triggered_by_id="h1",
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
    )
    node_runs = list(result.scalars().all())
    assert len(node_runs) == 1
    assert node_runs[0].status == "skipped"

    result = await db_session.execute(select(Message).where(Message.rivulet_id == rivulet.id))
    messages = list(result.scalars().all())
    assert all("should not run" not in m.content for m in messages)


async def test_conditional_node_passes_through_when_predicate_matches(
    db_session: AsyncSession,
) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="gate-pass")
    gate = WorkflowNode(
        workflow_id=workflow.id,
        name="gate",
        node_type="conditional",
        config_json=json.dumps({"contains": "urgent"}),
    )
    echo = _transform_node(workflow.id, "echo", "seen: {input}")
    db_session.add_all([gate, echo])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=gate.id),
            WorkflowConnection(workflow_id=workflow.id, from_node_id=gate.id, to_node_id=echo.id),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "URGENT: fix now", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message)
        .where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
        .order_by(Message.created_at)
    )
    outputs = [m.content for m in result.scalars().all()]
    assert outputs == ["URGENT: fix now", "seen: URGENT: fix now"]


async def test_agent_node_calls_run_agent_and_chains_its_output(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.db.models import Agent

    agent = Agent(
        name="Greeter",
        description="Greets people warmly enough to satisfy the 10-500 char minimum.",
        instructions="Say hi.",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="agent-flow")
    node = WorkflowNode(workflow_id=workflow.id, name="greet", node_type="agent", agent_id=agent.id)
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(get_content_as_string=lambda: "Hello there!")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    message = result.scalars().one()
    assert message.content == "Hello there!"
    assert message.sender_type == "agent"
    assert message.sender_id == agent.id


async def test_agent_node_is_blocked_by_a_tripped_hard_stop_budget_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#320: a workflow agent node is a spend path same as channel
    dispatch's -- #246 only wired dispatch/budgets.py's check into
    dispatch/service.py's _invoke_agent, leaving workflow agent nodes
    able to keep spending past a tripped workspace/agent hard_stop cap."""
    from rivulets.db.models import Agent, AgentRun, BudgetCap

    agent = Agent(
        name="Spendy",
        description="An agent whose prior spend already breaches its own budget cap.",
        instructions="Say hi.",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add(
        BudgetCap(
            scope_type="agent", agent_id=agent.id, period="day", limit_usd=0.5, action="hard_stop"
        )
    )
    # Already over the $0.50 cap before this run ever starts.
    db_session.add(AgentRun(agent_id=agent.id, model=agent.model, status="completed", cost_usd=1.0))

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="agent-flow")
    node = WorkflowNode(workflow_id=workflow.id, name="greet", node_type="agent", agent_id=agent.id)
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )
    await db_session.commit()

    calls: list[str] = []

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return SimpleNamespace(get_content_as_string=lambda: "Hello there!")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )

    assert calls == []  # the model was never invoked
    assert run.status == "failed"
    assert "budget cap" in (run.error_message or "").lower()
    assert "blocked" in (run.error_message or "").lower()


async def test_agent_node_is_blocked_by_a_team_cap_on_a_team_containing_the_agent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#354: a workflow has no team context to pass to the budget check,
    but a team-scope hard_stop on a team the agent belongs to must still
    block it -- before this, the same agent was blocked in channel
    dispatch (which passes channel.team_id) yet ran freely from a
    workflow node."""
    from rivulets.db.models import Agent, AgentRun, BudgetCap, Team, TeamAgent

    agent = Agent(
        name="Spendy",
        description="An agent whose team's prior spend already breaches its team budget cap.",
        instructions="Say hi.",
        model="anthropic:claude-3-5-haiku-latest",
    )
    team = Team(name="Capped Team")
    db_session.add_all([agent, team])
    await db_session.flush()
    db_session.add(TeamAgent(team_id=team.id, agent_id=agent.id))
    db_session.add(
        BudgetCap(
            scope_type="team", team_id=team.id, period="day", limit_usd=0.5, action="hard_stop"
        )
    )
    # Already over the team's $0.50 cap before this run ever starts.
    db_session.add(AgentRun(agent_id=agent.id, model=agent.model, status="completed", cost_usd=1.0))

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="team-capped-flow")
    node = WorkflowNode(workflow_id=workflow.id, name="greet", node_type="agent", agent_id=agent.id)
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )
    await db_session.commit()

    calls: list[str] = []

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return SimpleNamespace(get_content_as_string=lambda: "Hello there!")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )

    assert calls == []  # the model was never invoked
    assert run.status == "failed"
    assert "budget cap" in (run.error_message or "").lower()


async def _make_summarize_workflow(db_session: AsyncSession) -> tuple[Workflow, Rivulet]:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="summarize-flow")
    node = WorkflowNode(workflow_id=workflow.id, name="tldr", node_type="summarize")
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )
    await db_session.commit()
    return workflow, rivulet


async def test_summarize_node_is_blocked_by_a_tripped_workspace_hard_stop_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#354 (leftover of #320): a summarize node's LLM call is billed
    spend with no Agent row behind it -- a tripped workspace hard_stop
    must refuse it before the model is ever invoked, instead of failing
    open the way it did before the check existed."""
    from rivulets.db.models import AgentRun, BudgetCap

    db_session.add(
        BudgetCap(scope_type="workspace", period="day", limit_usd=0.5, action="hard_stop")
    )
    # Already over the $0.50 cap before this run ever starts.
    db_session.add(
        AgentRun(model="anthropic:claude-3-5-haiku-latest", status="completed", cost_usd=1.0)
    )
    workflow, rivulet = await _make_summarize_workflow(db_session)

    calls: list[str] = []

    async def fake_pick(*_args: object, **_kwargs: object) -> str:
        return "anthropic:claude-3-5-haiku-latest"

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def counting_summarizer(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return SimpleNamespace(content="a summary")

    monkeypatch.setattr("rivulets.dispatch.rule_generation.pick_dispatcher_model", fake_pick)
    monkeypatch.setattr("rivulets.workflows.nodes.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.workflows.nodes._run_summarizer", counting_summarizer)

    run = await run_workflow(
        db_session, workflow, rivulet, "long text", triggered_by="human", triggered_by_id="h1"
    )

    assert calls == []  # the model was never invoked
    assert run.status == "failed"
    assert "budget cap" in (run.error_message or "").lower()
    assert "blocked" in (run.error_message or "").lower()


async def test_summarize_node_records_its_spend_as_an_agent_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#354: summarize spend must count toward budget-cap windows and the
    usage dashboard -- recorded as an agent_id=None,
    source='summarize_node' row via the same record_agent_run choke point
    as every other billed call."""
    from rivulets.db.models import AgentRun

    workflow, rivulet = await _make_summarize_workflow(db_session)

    async def fake_pick(*_args: object, **_kwargs: object) -> str:
        return "anthropic:claude-3-5-haiku-latest"

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_summarizer(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            content="a summary",
            metrics=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    monkeypatch.setattr("rivulets.dispatch.rule_generation.pick_dispatcher_model", fake_pick)
    monkeypatch.setattr("rivulets.workflows.nodes.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.workflows.nodes._run_summarizer", fake_summarizer)

    run = await run_workflow(
        db_session, workflow, rivulet, "long text", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    assert result.scalars().one().content == "a summary"

    agent_run = (await db_session.scalars(select(AgentRun))).one()
    assert agent_run.source == "summarize_node"
    assert agent_run.agent_id is None
    assert agent_run.model == "anthropic:claude-3-5-haiku-latest"
    assert agent_run.total_tokens == 150
    # Priced model -- this spend lands in spend_usd, so it can trip a cap.
    assert agent_run.cost_usd is not None and agent_run.cost_usd > 0


async def test_node_retries_on_failure_then_succeeds(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.db.models import Agent

    agent = Agent(
        name="Flaky",
        description="An agent whose fake run_agent fails once before succeeding for tests.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="flaky-flow")
    node = WorkflowNode(
        workflow_id=workflow.id,
        name="flaky",
        node_type="agent",
        agent_id=agent.id,
        retry_max_attempts=1,
        retry_backoff_seconds=0,
    )
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )
    await db_session.commit()

    calls = {"count": 0}

    async def flaky_run_agent(*_args: object, **_kwargs: object) -> Any:
        from types import SimpleNamespace

        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient provider error")
        return SimpleNamespace(get_content_as_string=lambda: "recovered")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", flaky_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    assert calls["count"] == 2
    result = await db_session.execute(
        select(WorkflowNodeRun)
        .where(WorkflowNodeRun.workflow_run_id == run.id)
        .order_by(WorkflowNodeRun.attempt)
    )
    node_runs = list(result.scalars().all())
    assert [nr.status for nr in node_runs] == ["failed", "completed"]


async def test_node_failure_exhausting_retries_stops_the_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.db.models import Agent

    agent = Agent(
        name="AlwaysFails",
        description="An agent whose fake run_agent always raises, for retry-exhaustion tests.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="doomed-flow")
    node = WorkflowNode(
        workflow_id=workflow.id,
        name="doomed",
        node_type="agent",
        agent_id=agent.id,
        retry_max_attempts=1,
        retry_backoff_seconds=0,
    )
    unreachable = _transform_node(workflow.id, "unreachable", "never: {input}")
    db_session.add_all([node, unreachable])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=node.id, to_node_id=unreachable.id
            ),
        ]
    )
    await db_session.commit()

    async def always_fails(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider is down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", always_fails)

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "provider is down" in (run.error_message or "")
    result = await db_session.execute(
        select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
    )
    node_runs = list(result.scalars().all())
    assert len(node_runs) == 2  # initial attempt + 1 retry, both failed
    assert all(nr.status == "failed" for nr in node_runs)

    result = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    alert = result.scalars().one()
    assert "doomed" in alert.content


async def test_on_failure_workflow_id_triggers_remediation_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#94 layer 2: a failed run of a workflow with `on_failure_workflow_id`
    set automatically triggers a fresh top-level run of that remediation
    workflow, with the failure's context as its input."""
    from rivulets.db.models import Agent

    agent = Agent(
        name="AlwaysFails2",
        description="An agent whose fake run_agent always raises, for remediation tests.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    fixer = await _make_workflow(db_session, name="fixer")
    fixer.published = True
    fixer_node = _transform_node(fixer.id, "recover", "recovered: {input}")
    db_session.add(fixer_node)
    await db_session.flush()
    await _entry_connect(db_session, fixer.id, fixer_node.id)

    doomed = await _make_workflow(db_session, name="doomed-flow-2")
    doomed.on_failure_workflow_id = fixer.id
    doomed_node = WorkflowNode(
        workflow_id=doomed.id, name="doomed", node_type="agent", agent_id=agent.id
    )
    db_session.add(doomed_node)
    await db_session.flush()
    await _entry_connect(db_session, doomed.id, doomed_node.id)
    await db_session.commit()

    async def always_fails(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider is down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", always_fails)

    run = await run_workflow(
        db_session, doomed, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "failed"

    result = await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == fixer.id)
    )
    remediation_runs = list(result.scalars().all())
    assert len(remediation_runs) == 1
    remediation_run = remediation_runs[0]
    assert remediation_run.triggered_by == "remediation"
    assert remediation_run.triggered_by_id == run.id
    assert remediation_run.status == "completed"
    assert remediation_run.final_output is not None
    assert "recovered: Workflow /doomed-flow-2 run failed." in remediation_run.final_output
    assert "provider is down" in remediation_run.final_output

    result = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    alerts = [m.content for m in result.scalars().all()]
    assert any("triggering remediation workflow /fixer" in a for a in alerts)


async def test_on_failure_workflow_id_does_not_chain_past_one_remediation_attempt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#94 layer 2's depth-1 cap: a self-referencing (or cyclically
    referencing) on_failure_workflow_id can trigger at most one
    remediation attempt -- a run whose own triggered_by is already
    'remediation' never triggers further remediation, so this doesn't
    ping-pong forever."""
    from rivulets.db.models import Agent

    agent = Agent(
        name="AlwaysFails3",
        description="An agent whose fake run_agent always raises, for the depth-cap test.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="self-doomed")
    node = WorkflowNode(
        workflow_id=workflow.id, name="doomed", node_type="agent", agent_id=agent.id
    )
    db_session.add(node)
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, node.id)
    workflow.published = True  # the remediation target (itself) must be published too, #292
    workflow.on_failure_workflow_id = workflow.id  # retries itself once on failure
    await db_session.commit()

    async def always_fails(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("still down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", always_fails)

    run = await run_workflow(
        db_session, workflow, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "failed"

    result = await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
    )
    all_runs = list(result.scalars().all())
    # The original human-triggered run plus exactly one remediation
    # attempt -- not a third, chained remediation-of-the-remediation.
    assert len(all_runs) == 2
    assert {r.triggered_by for r in all_runs} == {"human", "remediation"}
    remediation_run = next(r for r in all_runs if r.triggered_by == "remediation")
    assert remediation_run.status == "failed"
    assert remediation_run.triggered_by_id == run.id


async def test_on_failure_workflow_id_skips_an_unpublished_remediation_target(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#292: `_maybe_trigger_remediation` re-checks `published` on the
    remediation target, the same gate #249 added to
    `_execute_workflow_node`'s nested 'workflow' node -- an unpublished
    draft configured as `on_failure_workflow_id` (e.g. synced from a peer
    that had built but not yet published it) must not fire unattended."""
    from rivulets.db.models import Agent

    agent = Agent(
        name="AlwaysFails4",
        description="An agent whose fake run_agent always raises, for the unpublished test.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add(agent)
    await db_session.flush()

    rivulet = await _make_rivulet(db_session)
    fixer = await _make_workflow(db_session, name="draft-fixer")  # published=False, default
    fixer_node = _transform_node(fixer.id, "recover", "recovered: {input}")
    db_session.add(fixer_node)
    await db_session.flush()
    await _entry_connect(db_session, fixer.id, fixer_node.id)

    doomed = await _make_workflow(db_session, name="doomed-flow-3")
    doomed.on_failure_workflow_id = fixer.id
    doomed_node = WorkflowNode(
        workflow_id=doomed.id, name="doomed", node_type="agent", agent_id=agent.id
    )
    db_session.add(doomed_node)
    await db_session.flush()
    await _entry_connect(db_session, doomed.id, doomed_node.id)
    await db_session.commit()

    async def always_fails(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider is down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", always_fails)

    run = await run_workflow(
        db_session, doomed, rivulet, "hi", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "failed"

    result = await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == fixer.id)
    )
    assert result.scalars().all() == []

    result = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    alerts = [m.content for m in result.scalars().all()]
    assert not any("triggering remediation workflow" in a for a in alerts)


async def test_on_call_agent_falls_back_to_workspace_default(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#94 layer 3: with Workflow.on_call_agent_id left unset, a failed run
    still @mentions the workspace-wide 'workflows.default_on_call_agent_id'
    setting -- and, since that's a real @mention run through the ordinary
    dispatch path, the mentioned agent's own reply lands in the rivulet
    too, not just the alert."""
    from rivulets.db.models import Agent, Team, TeamAgent, WorkspaceSetting

    oncall = Agent(
        name="OncallDefault",
        description="Workspace-default on-call agent for the fallback test.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    doomed_agent = Agent(
        name="AlwaysFails4",
        description="An agent whose fake run_agent always raises, for the on-call test.",
        instructions="n/a",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db_session.add_all([oncall, doomed_agent])
    await db_session.flush()

    team = Team(name="Oncall Team")
    db_session.add(team)
    await db_session.flush()
    db_session.add(TeamAgent(team_id=team.id, agent_id=oncall.id))
    db_session.add(
        WorkspaceSetting(key="workflows.default_on_call_agent_id", value=json.dumps(oncall.id))
    )

    channel = Channel(name="wf-oncall-test", team_id=team.id)
    db_session.add(channel)
    await db_session.flush()
    rivulet = Rivulet(channel_id=channel.id, created_by="human")
    db_session.add(rivulet)
    await db_session.flush()

    workflow = await _make_workflow(db_session, name="flaky-default")
    node = WorkflowNode(
        workflow_id=workflow.id, name="doomed", node_type="agent", agent_id=doomed_agent.id
    )
    db_session.add(node)
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, node.id)
    await db_session.commit()
    # workflow.on_call_agent_id stays None -- must fall back to the
    # workspace default set above.

    async def doomed_run_agent(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("upstream is down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", doomed_run_agent)

    async def oncall_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[],
            get_content_as_string=lambda: "Looking into it.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", oncall_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "failed"

    result = await db_session.execute(select(Message).where(Message.rivulet_id == rivulet.id))
    contents = [(m.sender_type, m.sender_name, m.content) for m in result.scalars().all()]
    assert any(
        sender_type == "system" and "@OncallDefault" in content
        for sender_type, _, content in contents
    )
    assert ("agent", "OncallDefault", "Looking into it.") in contents


async def test_conditioned_edges_route_to_only_the_matching_branch(
    db_session: AsyncSession,
) -> None:
    """#81: real branching -- an edge's own condition_json, not the node's
    config, decides which of several outbound edges get followed."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="triage")
    gate = _transform_node(workflow.id, "gate", "{input}")
    urgent_path = _transform_node(workflow.id, "urgent-path", "PAGE: {input}")
    normal_path = _transform_node(workflow.id, "normal-path", "queued: {input}")
    db_session.add_all([gate, urgent_path, normal_path])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=gate.id),
            WorkflowConnection(
                workflow_id=workflow.id,
                from_node_id=gate.id,
                to_node_id=urgent_path.id,
                condition_json=json.dumps({"contains": "urgent"}),
            ),
            WorkflowConnection(
                workflow_id=workflow.id,
                from_node_id=gate.id,
                to_node_id=normal_path.id,
                condition_json=json.dumps({"not_contains": "urgent"}),
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "URGENT: fix now", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    outputs = [m.content for m in result.scalars().all()]
    assert outputs == ["URGENT: fix now", "PAGE: URGENT: fix now"]


async def test_unconditioned_edges_fan_out_in_parallel(db_session: AsyncSession) -> None:
    """#81: a node with multiple always-matching outbound edges runs all
    of them, not just the first."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="fanout")
    start = _transform_node(workflow.id, "start", "{input}")
    branch_a = _transform_node(workflow.id, "branch-a", "A: {input}")
    branch_b = _transform_node(workflow.id, "branch-b", "B: {input}")
    db_session.add_all([start, branch_a, branch_b])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=start.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=start.id, to_node_id=branch_a.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=start.id, to_node_id=branch_b.id
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    outputs = {m.content for m in result.scalars().all()}
    assert outputs == {"go", "A: go", "B: go"}


async def test_unbounded_loop_trips_the_loop_guard(db_session: AsyncSession) -> None:
    """#81: a node whose outbound edge points back at itself is a valid
    graph shape (loops fall out of allowing any edge, not a dedicated
    'loop node') but must be bounded -- MAX_NODE_VISITS_PER_RUN caps it."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="spin")
    loop_node = _transform_node(workflow.id, "spin", "{input}")
    db_session.add(loop_node)
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=loop_node.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=loop_node.id, to_node_id=loop_node.id
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "loop guard" in (run.error_message or "")
    result = await db_session.execute(
        select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
    )
    node_runs = list(result.scalars().all())
    assert len(node_runs) == MAX_NODE_VISITS_PER_RUN

    alerts = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    assert "loop guard" in alerts.scalars().one().content


async def test_merge_node_joins_sibling_branches_as_a_json_array(
    db_session: AsyncSession,
) -> None:
    """#82: two branches that fan out from the same node and reconverge at
    a merge node get joined into ONE execution, not one per arrival --
    the default (no config.template) strategy is a JSON array, in the
    fanned-out edges' creation order, not arrival order."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="converge")
    start = _transform_node(workflow.id, "start", "{input}")
    branch_a = _transform_node(workflow.id, "branch-a", "A: {input}")
    branch_b = _transform_node(workflow.id, "branch-b", "B: {input}")
    merge = WorkflowNode(workflow_id=workflow.id, name="merge", node_type="merge")
    db_session.add_all([start, branch_a, branch_b, merge])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=start.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=start.id, to_node_id=branch_a.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=start.id, to_node_id=branch_b.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=branch_a.id, to_node_id=merge.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=branch_b.id, to_node_id=merge.id
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(WorkflowNodeRun).where(WorkflowNodeRun.node_id == merge.id)
    )
    merge_runs = list(result.scalars().all())
    assert len(merge_runs) == 1
    assert merge_runs[0].output_content == json.dumps(["A: go", "B: go"])
    assert json.loads(merge_runs[0].input_content) == ["A: go", "B: go"]


async def test_merge_node_template_mode_combines_sibling_outputs(
    db_session: AsyncSession,
) -> None:
    """#82's "beyond naive concatenation" strategy: config.template with
    one indexed placeholder per contributing branch."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="converge-template")
    start = _transform_node(workflow.id, "start", "{input}")
    branch_a = _transform_node(workflow.id, "branch-a", "left")
    branch_b = _transform_node(workflow.id, "branch-b", "right")
    merge = WorkflowNode(
        workflow_id=workflow.id,
        name="merge",
        node_type="merge",
        config_json=json.dumps({"template": "{input0} vs {input1}"}),
    )
    db_session.add_all([start, branch_a, branch_b, merge])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=start.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=start.id, to_node_id=branch_a.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=start.id, to_node_id=branch_b.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=branch_a.id, to_node_id=merge.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=branch_b.id, to_node_id=merge.id
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    outputs = [m.content for m in result.scalars().all()]
    assert outputs[-1] == "left vs right"


async def test_merge_node_joins_siblings_at_different_hop_depths(
    db_session: AsyncSession,
) -> None:
    """#82: siblings don't need to be the same number of hops from the
    merge node to join -- gate-a fans out to [gate-b -> merge] and
    [merge directly]; despite one path being a hop longer, both still
    trace back to gate-a as their nearest common fan-out, so they're
    still recognized as one group and the merge runs once, not twice."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="asymmetric-converge")
    gate_a = WorkflowNode(
        workflow_id=workflow.id,
        name="gate-a",
        node_type="conditional",
        config_json=json.dumps({"contains": "go"}),
    )
    gate_b = WorkflowNode(
        workflow_id=workflow.id,
        name="gate-b",
        node_type="conditional",
        config_json=json.dumps({"contains": "go"}),
    )
    merge = WorkflowNode(workflow_id=workflow.id, name="merge", node_type="merge")
    db_session.add_all([gate_a, gate_b, merge])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=gate_a.id),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=gate_a.id, to_node_id=gate_b.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=gate_a.id, to_node_id=merge.id
            ),
            WorkflowConnection(
                workflow_id=workflow.id, from_node_id=gate_b.id, to_node_id=merge.id
            ),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(WorkflowNodeRun).where(WorkflowNodeRun.node_id == merge.id)
    )
    merge_runs = list(result.scalars().all())
    assert len(merge_runs) == 1
    assert json.loads(merge_runs[0].input_content) == ["go", "go"]


async def test_merge_node_with_single_arrival_still_wraps_as_json_array(
    db_session: AsyncSession,
) -> None:
    """A merge node's output shape doesn't depend on how many branches
    arrived -- even a lone arrival gets wrapped, not passed through bare,
    so downstream nodes can always expect the same shape."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="merge-flow")
    merge = WorkflowNode(workflow_id=workflow.id, name="merge", node_type="merge")
    db_session.add(merge)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=merge.id)
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "unchanged", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    assert result.scalars().one().content == json.dumps(["unchanged"])


async def test_human_input_node_pauses_the_run_and_rivulet(db_session: AsyncSession) -> None:
    """#83: a 'human_input' node stops the run rather than executing --
    WorkflowRun/WorkflowNodeRun both record 'awaiting_human', and
    Rivulet.status='paused' mirrors dispatch/guards.py's loop-guard pause
    exactly (so the channel UI's existing paused banner needs no changes
    to surface it)."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="ask-flow")
    ask = WorkflowNode(workflow_id=workflow.id, name="ask", node_type="human_input")
    db_session.add(ask)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=ask.id)
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "please confirm", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "awaiting_human"
    assert run.current_node_id == ask.id
    assert run.completed_at is None
    assert rivulet.status == "paused"

    result = await db_session.execute(
        select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
    )
    node_runs = list(result.scalars().all())
    assert len(node_runs) == 1
    assert node_runs[0].status == "awaiting_human"
    assert node_runs[0].output_content is None

    result = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    alert = result.scalars().one()
    assert "ask" in alert.content
    assert "waiting for your reply" in alert.content


async def test_resume_workflow_continues_with_the_reply_as_output(
    db_session: AsyncSession,
) -> None:
    """#83: resume_workflow feeds the human's reply into the paused node's
    outbound edges as if it were that node's own output -- the same "a
    node's output becomes the next node's input" contract every other
    node type honors."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="ask-then-echo")
    ask = WorkflowNode(workflow_id=workflow.id, name="ask", node_type="human_input")
    echo = _transform_node(workflow.id, "echo", "confirmed: {input}")
    db_session.add_all([ask, echo])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=ask.id),
            WorkflowConnection(workflow_id=workflow.id, from_node_id=ask.id, to_node_id=echo.id),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "please confirm", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "awaiting_human"

    resumed = await resume_workflow(db_session, run, "yes please")

    assert resumed.status == "completed"
    assert resumed.completed_at is not None
    assert rivulet.status == "active"

    result = await db_session.execute(
        select(WorkflowNodeRun)
        .where(WorkflowNodeRun.node_id == ask.id)
        .order_by(WorkflowNodeRun.started_at)
    )
    ask_run = result.scalars().one()
    assert ask_run.status == "completed"
    assert ask_run.output_content == "yes please"

    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    assert result.scalars().one().content == "confirmed: yes please"


async def test_resume_workflow_can_pause_again_at_a_second_human_input_node(
    db_session: AsyncSession,
) -> None:
    """A resumed run can hit another 'human_input' node and pause again --
    resume_workflow isn't a one-shot unwind, it's the same walk mechanism
    as the original run, just re-entered at a different starting point."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="ask-twice")
    ask1 = WorkflowNode(workflow_id=workflow.id, name="ask1", node_type="human_input")
    ask2 = WorkflowNode(workflow_id=workflow.id, name="ask2", node_type="human_input")
    db_session.add_all([ask1, ask2])
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=ask1.id),
            WorkflowConnection(workflow_id=workflow.id, from_node_id=ask1.id, to_node_id=ask2.id),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "start", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "awaiting_human"
    assert run.current_node_id == ask1.id

    run = await resume_workflow(db_session, run, "first reply")
    assert run.status == "awaiting_human"
    assert run.current_node_id == ask2.id
    assert rivulet.status == "paused"

    run = await resume_workflow(db_session, run, "second reply")
    assert run.status == "completed"
    assert rivulet.status == "active"


async def test_loop_guard_survives_repeated_pause_resume_cycles(
    db_session: AsyncSession,
) -> None:
    """#249: a graph that loops back onto its own 'human_input' node pauses
    once per iteration -- before the loop guard's counters were persisted
    onto WorkflowRun, each resume_workflow call rebuilt a fresh
    `_RunContext` and this could pause/resume past MAX_NODE_VISITS_PER_RUN
    forever without ever tripping. Resuming enough times to cross that cap
    must now fail the run instead."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="ask-forever")
    ask = WorkflowNode(workflow_id=workflow.id, name="ask", node_type="human_input")
    db_session.add(ask)
    await db_session.flush()
    db_session.add_all(
        [
            WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=ask.id),
            WorkflowConnection(workflow_id=workflow.id, from_node_id=ask.id, to_node_id=ask.id),
        ]
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "start", triggered_by="human", triggered_by_id="h1"
    )
    assert run.status == "awaiting_human"  # visit 1

    # Visits 2 through MAX_NODE_VISITS_PER_RUN + 1 -- the last of these
    # crosses the cap and fails the run instead of pausing again.
    for _ in range(MAX_NODE_VISITS_PER_RUN):
        run = await resume_workflow(db_session, run, "again")

    assert run.status == "failed"
    assert "loop guard" in (run.error_message or "")


async def test_run_final_output_reflects_the_terminal_nodes_output(
    db_session: AsyncSession,
) -> None:
    """#85: WorkflowRun.final_output didn't exist before nesting needed a
    queryable "this run's result" -- verify the plumbing on an ordinary,
    non-nested run first."""
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="final-output")
    step = _transform_node(workflow.id, "step", "done: {input}")
    db_session.add(step)
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, step.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    assert run.final_output == "done: go"


async def test_workflow_node_invokes_a_nested_workflow_and_chains_its_output(
    db_session: AsyncSession,
) -> None:
    """#85: a 'workflow' node runs the referenced workflow as a nested
    WorkflowRun and its final_output becomes this node's own output --
    the same "output becomes the next input" contract every node type
    honors."""
    rivulet = await _make_rivulet(db_session)

    child = await _make_workflow(db_session, name="child-flow")
    child.published = True
    child_step = _transform_node(child.id, "child-step", "child saw: {input}")
    db_session.add(child_step)
    await db_session.flush()
    await _entry_connect(db_session, child.id, child_step.id)

    parent = await _make_workflow(db_session, name="parent-flow")
    invoke = _workflow_node(parent.id, "invoke-child", child.id)
    wrap = _transform_node(parent.id, "wrap", "parent got: {input}")
    db_session.add_all([invoke, wrap])
    await db_session.flush()
    await _entry_connect(db_session, parent.id, invoke.id)
    db_session.add(
        WorkflowConnection(workflow_id=parent.id, from_node_id=invoke.id, to_node_id=wrap.id)
    )
    await db_session.commit()

    run = await run_workflow(
        db_session, parent, rivulet, "hello", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    assert run.final_output == "parent got: child saw: hello"

    result = await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == child.id)
    )
    child_run = result.scalars().one()
    assert child_run.status == "completed"
    assert child_run.triggered_by == "workflow"
    assert child_run.triggered_by_id == run.id
    assert child_run.final_output == "child saw: hello"

    # Both runs post into the same rivulet, interleaved: the child's own
    # output, then the parent's "invoke-child" node relaying that same
    # value as its own output (unmodified, same as any other node type),
    # then "wrap" transforming it.
    result = await db_session.execute(
        select(Message).where(Message.rivulet_id == rivulet.id, Message.content_type == "text")
    )
    contents = [m.content for m in result.scalars().all()]
    assert contents == [
        "child saw: hello",
        "child saw: hello",
        "parent got: child saw: hello",
    ]


async def test_workflow_node_rejects_a_direct_self_cycle(db_session: AsyncSession) -> None:
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_workflow(db_session, name="self-embed")
    invoke = _workflow_node(workflow.id, "invoke-self", workflow.id)
    db_session.add(invoke)
    await db_session.flush()
    await _entry_connect(db_session, workflow.id, invoke.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "cycle" in (run.error_message or "")


async def test_workflow_node_rejects_an_indirect_cycle(db_session: AsyncSession) -> None:
    """A embeds B, B embeds A -- api/workflows.py's create-time check can
    only catch a *direct* self-reference (see _validate_child_workflow's
    docstring); this multi-hop case is only catchable at run time, via
    the engine's own ancestry-based guard."""
    rivulet = await _make_rivulet(db_session)
    workflow_a = await _make_workflow(db_session, name="workflow-a")
    workflow_b = await _make_workflow(db_session, name="workflow-b")
    workflow_b.published = True

    invoke_b = _workflow_node(workflow_a.id, "invoke-b", workflow_b.id)
    db_session.add(invoke_b)
    await db_session.flush()
    await _entry_connect(db_session, workflow_a.id, invoke_b.id)

    invoke_a = _workflow_node(workflow_b.id, "invoke-a", workflow_a.id)
    db_session.add(invoke_a)
    await db_session.flush()
    await _entry_connect(db_session, workflow_b.id, invoke_a.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, workflow_a, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "cycle" in (run.error_message or "")


async def test_nested_workflow_pausing_fails_the_parent_and_the_child(
    db_session: AsyncSession,
) -> None:
    """#85's documented boundary: a nested child pausing on 'human_input'
    isn't propagated -- it fails the parent's 'workflow' node, and the
    child run is failed too (not left dangling in 'awaiting_human',
    discoverable by a later reply with no parent left waiting on it)."""
    rivulet = await _make_rivulet(db_session)

    child = await _make_workflow(db_session, name="asks-a-question")
    child.published = True
    ask = WorkflowNode(workflow_id=child.id, name="ask", node_type="human_input")
    db_session.add(ask)
    await db_session.flush()
    await _entry_connect(db_session, child.id, ask.id)

    parent = await _make_workflow(db_session, name="calls-asker")
    invoke = _workflow_node(parent.id, "invoke-child", child.id)
    db_session.add(invoke)
    await db_session.flush()
    await _entry_connect(db_session, parent.id, invoke.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, parent, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "paused for human input" in (run.error_message or "")
    assert rivulet.status == "active"

    result = await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == child.id)
    )
    child_run = result.scalars().one()
    assert child_run.status == "failed"


async def test_workflow_node_rejects_an_unpublished_child_workflow(
    db_session: AsyncSession,
) -> None:
    """#249: nested runs must require `published`, the same gate every
    other trigger path (slash-command, schedule, webhook) already
    enforces -- a child workflow still being built in the canvas (default
    `published=False`, see `_make_workflow`) can't be nest-run just
    because a 'workflow' node references it."""
    rivulet = await _make_rivulet(db_session)

    child = await _make_workflow(db_session, name="unpublished-child")
    child_step = _transform_node(child.id, "child-step", "child saw: {input}")
    db_session.add(child_step)
    await db_session.flush()
    await _entry_connect(db_session, child.id, child_step.id)

    parent = await _make_workflow(db_session, name="calls-unpublished-child")
    invoke = _workflow_node(parent.id, "invoke-child", child.id)
    db_session.add(invoke)
    await db_session.flush()
    await _entry_connect(db_session, parent.id, invoke.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, parent, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "isn't published" in (run.error_message or "")


async def test_workflow_node_rejects_nesting_deeper_than_the_cap(
    db_session: AsyncSession,
) -> None:
    """#249: MAX_WORKFLOW_NESTING_DEPTH bounds a long *acyclic* chain of
    distinct nested workflows -- unlike the ancestry-based cycle guard
    (which only catches a workflow embedding itself, directly or
    indirectly), nothing else stops one workflow embedding the next
    embedding the next indefinitely."""
    rivulet = await _make_rivulet(db_session)

    depth = MAX_WORKFLOW_NESTING_DEPTH + 1
    chain = [await _make_workflow(db_session, name=f"chain-{i}") for i in range(depth)]
    for workflow in chain:
        workflow.published = True
    for i in range(depth - 1):
        invoke = _workflow_node(chain[i].id, f"invoke-{i + 1}", chain[i + 1].id)
        db_session.add(invoke)
        await db_session.flush()
        await _entry_connect(db_session, chain[i].id, invoke.id)
    await db_session.commit()

    run = await run_workflow(
        db_session, chain[0], rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "failed"
    assert "nest workflows" in (run.error_message or "")


# --- #360: builtin side-effect tools called from a workflow agent node --


def _tool_call_output(reply: str, tool_name: str, tool_args: dict[str, Any]) -> RunOutput:
    """A real RunOutput (not a SimpleNamespace double) because #360's
    trigger path inspects `.tools` the same way dispatch/service.py's
    channel path does -- mirrors test_channel_tools.py's fixtures."""
    return RunOutput(
        status=RunStatus.completed,
        content=reply,
        tools=[ToolExecution(tool_name=tool_name, tool_args=tool_args)],
    )


async def _make_agent(db: AsyncSession, name: str) -> Agent:
    agent = Agent(
        name=name,
        description="A workflow-node agent double for the #360 side-effect trigger tests.",
        instructions="Do the thing.",
        model="anthropic:claude-3-5-haiku-latest",
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_agent_node_workflow(db: AsyncSession, name: str, agent: Agent) -> Workflow:
    """One published workflow whose single node is an agent node -- the
    minimal graph every #360 test drives."""
    workflow = await _make_workflow(db, name=name)
    workflow.published = True
    node = WorkflowNode(workflow_id=workflow.id, name="act", node_type="agent", agent_id=agent.id)
    db.add(node)
    await db.flush()
    await _entry_connect(db, workflow.id, node.id)
    return workflow


async def test_agent_node_run_workflow_tool_call_starts_a_child_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#360: before this, the run_workflow stub's "Requested..." reply was
    the only thing that happened -- the agent could *say* it ran another
    workflow while no child WorkflowRun was ever created."""
    agent = await _make_agent(db_session, "Launcher")
    rivulet = await _make_rivulet(db_session)

    child = await _make_workflow(db_session, name="child-flow")
    child.published = True
    echo = _transform_node(child.id, "echo", "child saw: {input}")
    db_session.add(echo)
    await db_session.flush()
    await _entry_connect(db_session, child.id, echo.id)

    parent = await _make_agent_node_workflow(db_session, "parent-flow", agent)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return _tool_call_output(
            "Kicking it off.",
            "run_workflow",
            {"workflow_name": "child-flow", "workflow_input": "ping"},
        )

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, parent, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    child_run = await db_session.scalar(
        select(WorkflowRun).where(WorkflowRun.workflow_id == child.id)
    )
    assert child_run is not None
    assert child_run.status == "completed"
    assert child_run.triggered_by == "agent"
    assert child_run.triggered_by_id == agent.id
    assert child_run.final_output == "child saw: ping"


async def test_agent_node_triggered_child_run_inherits_unattendedness(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#360: a run_workflow trigger from inside a scheduled (unattended)
    run must stay unattended -- same inheritance _execute_workflow_node
    gives a nested 'workflow' node (#100) -- rather than re-deriving False
    from the child's literal 'agent' trigger."""
    agent = await _make_agent(db_session, "NightLauncher")
    rivulet = await _make_rivulet(db_session)

    child = await _make_workflow(db_session, name="night-child")
    child.published = True
    echo = _transform_node(child.id, "echo", "ok: {input}")
    db_session.add(echo)
    await db_session.flush()
    await _entry_connect(db_session, child.id, echo.id)

    parent = await _make_agent_node_workflow(db_session, "night-parent", agent)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return _tool_call_output(
            "Going.", "run_workflow", {"workflow_name": "night-child", "workflow_input": "x"}
        )

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, parent, rivulet, "go", triggered_by="schedule", triggered_by_id="sched-1"
    )

    assert run.status == "completed"
    assert run.unattended is True
    child_run = await db_session.scalar(
        select(WorkflowRun).where(WorkflowRun.workflow_id == child.id)
    )
    assert child_run is not None
    assert child_run.unattended is True


async def test_agent_node_run_workflow_self_trigger_is_cycle_guarded(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#360: an agent node calling run_workflow on the workflow it's
    running inside must not recurse -- engine.py's ancestry guard only
    covered the 'workflow' node path, so the agent-trigger path threads
    the same ancestry into _handle_run_workflow_trigger."""
    agent = await _make_agent(db_session, "Ouroboros")
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_agent_node_workflow(db_session, "loopy", agent)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return _tool_call_output(
            "Again!", "run_workflow", {"workflow_name": "loopy", "workflow_input": "again"}
        )

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    runs = (
        await db_session.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id))
    ).all()
    assert len(runs) == 1


async def test_agent_node_schedule_workflow_tool_call_creates_a_schedule(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session, "Planner")
    rivulet = await _make_rivulet(db_session)

    target = await _make_workflow(db_session, name="nightly")
    target.published = True

    parent = await _make_agent_node_workflow(db_session, "scheduler-flow", agent)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return _tool_call_output(
            "Scheduled.",
            "schedule_workflow",
            {"workflow_name": "nightly", "cron_expression": "0 9 * * *", "input_content": "go"},
        )

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, parent, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    schedule = await db_session.scalar(
        select(WorkflowSchedule).where(WorkflowSchedule.created_by == agent.id)
    )
    assert schedule is not None
    assert schedule.workflow_id == target.id
    assert schedule.cron_expression == "0 9 * * *"

    # The handler's confirmation message lands in the run's own rivulet,
    # committed/published by execute_agent_node itself (the engine has no
    # outer request handler to do it, unlike channel dispatch).
    result = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    alerts = [m.content for m in result.scalars().all()]
    assert any("schedul" in content.lower() for content in alerts)


async def test_agent_node_create_channel_requires_tool_assignment(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#360 keeps #240's invocation-time authorization: a create_channel
    call from an agent that was never assigned the builtin tool (or its
    channels:manage scope) stays a no-op on the workflow path too."""
    agent = await _make_agent(db_session, "Unauthorized")
    rivulet = await _make_rivulet(db_session)
    workflow = await _make_agent_node_workflow(db_session, "channel-flow", agent)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return _tool_call_output("Made it.", "create_channel", {"name": "roadmap"})

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    created = await db_session.scalar(select(Channel).where(Channel.name == "roadmap"))
    assert created is None


async def test_agent_node_create_channel_with_assignment_creates_the_channel(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session, "ChannelMaker")
    tool = Tool(
        name="create_channel",
        description="Creates channels.",
        tool_type="builtin",
        required_scope="channels:manage",
    )
    db_session.add(tool)
    await db_session.flush()
    db_session.add(AgentTool(agent_id=agent.id, tool_id=tool.id))
    db_session.add(AgentToolScope(agent_id=agent.id, scope="channels:manage"))

    rivulet = await _make_rivulet(db_session)
    workflow = await _make_agent_node_workflow(db_session, "channel-flow-authed", agent)
    await db_session.commit()

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return _tool_call_output("Made it.", "create_channel", {"name": "roadmap"})

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", fake_run_agent)

    run = await run_workflow(
        db_session, workflow, rivulet, "go", triggered_by="human", triggered_by_id="h1"
    )

    assert run.status == "completed"
    created = await db_session.scalar(select(Channel).where(Channel.name == "roadmap"))
    assert created is not None

    result = await db_session.execute(
        select(Message).where(
            Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
        )
    )
    alerts = [m.content for m in result.scalars().all()]
    assert any("created channel /roadmap" in content for content in alerts)
