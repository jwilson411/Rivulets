"""#95: EvalSuite execution (evals/runner.py), exercised directly against a
DB session -- mirrors test_workflow_engine.py's split between engine-level
and API-level coverage. Agent-suite cases monkeypatch
`rivulets.evals.runner.run_agent` the same way test_rivulet_dispatch.py and
test_workflow_engine.py monkeypatch it for their own modules, so no real
LLM call happens. Workflow-suite cases use a deterministic 'transform' node
(no agent involved at all), same as test_workflow_engine.py's own linear-
chain tests.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import (
    Agent,
    Channel,
    EvalCase,
    EvalCaseResult,
    EvalSuite,
    Message,
    Rivulet,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowRun,
)
from rivulets.evals.runner import run_eval_suite


def _fake_run_agent(content: str, tools: list[Any] | None = None) -> Any:
    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=tools, get_content_as_string=lambda: content
        )

    return fake


async def _make_agent(db: AsyncSession, name: str = "Grader") -> Agent:
    agent = Agent(
        name=name, description="d", instructions="i", model="anthropic:claude-haiku-4-5-20251001"
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_workflow(db: AsyncSession, name: str = "flow") -> Workflow:
    workflow = Workflow(name=name)
    db.add(workflow)
    await db.flush()
    return workflow


def _exact_case(suite_id: str, name: str, input_content: str, expected_output: str) -> EvalCase:
    return EvalCase(
        suite_id=suite_id,
        name=name,
        input_content=input_content,
        judge_type="exact",
        expected_output=expected_output,
    )


async def test_run_eval_suite_agent_suite_scores_exact_match(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="s1", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "greets", "hi", "hello"))
    await db_session.commit()

    monkeypatch.setattr("rivulets.evals.runner.run_agent", _fake_run_agent("hello"))

    run = await run_eval_suite(db_session, suite, human_id="human-1")

    assert run.status == "completed"
    assert run.case_count == 1
    assert run.pass_count == 1
    assert run.fail_count == 0
    assert run.error_count == 0

    result = (
        await db_session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    ).one()
    assert result.status == "passed"
    assert result.actual_output == "hello"


async def test_run_eval_suite_records_failed_match(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="s2", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "greets", "hi", "hello"))
    await db_session.commit()

    monkeypatch.setattr("rivulets.evals.runner.run_agent", _fake_run_agent("goodbye"))

    run = await run_eval_suite(db_session, suite, human_id=None)
    assert run.fail_count == 1
    assert run.pass_count == 0


async def test_run_eval_suite_records_execution_error_without_aborting_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One case's execution blowing up (e.g. the agent's provider is
    unreachable) must not abort the rest of the suite run -- captured as
    that case's own 'error' result instead."""
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="s3", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "case-a", "hi", "hello"))
    db_session.add(_exact_case(suite.id, "case-b", "yo", "hi"))
    await db_session.commit()

    async def boom(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("rivulets.evals.runner.run_agent", boom)

    run = await run_eval_suite(db_session, suite, human_id=None)
    assert run.case_count == 2
    assert run.error_count == 2
    assert run.pass_count == 0


async def test_run_eval_suite_case_is_blocked_by_a_tripped_hard_stop_budget_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#320: an eval-suite agent case is a spend path same as channel
    dispatch's -- #246 only wired dispatch/budgets.py's check into
    dispatch/service.py's _invoke_agent, leaving eval runs able to keep
    spending past a tripped hard_stop cap. Blocked before the model is
    ever called, same "in-flight work finishes, only the next call is
    refused" semantics as test_budget_enforcement.py's channel-dispatch
    coverage -- here that just means the very first (only) case."""
    from rivulets.db.models import AgentRun, BudgetCap

    agent = await _make_agent(db_session, name="Spendy")
    db_session.add(
        BudgetCap(
            scope_type="agent", agent_id=agent.id, period="day", limit_usd=0.5, action="hard_stop"
        )
    )
    # Already over the $0.50 cap before this run ever starts.
    db_session.add(AgentRun(agent_id=agent.id, model=agent.model, status="completed", cost_usd=1.0))
    suite = EvalSuite(name="s-budget", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "greets", "hi", "hello"))
    await db_session.commit()

    calls: list[str] = []

    async def counting_fake(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return await _fake_run_agent("hello")()

    monkeypatch.setattr("rivulets.evals.runner.run_agent", counting_fake)

    run = await run_eval_suite(db_session, suite, human_id=None)

    assert calls == []  # the model was never invoked
    assert run.case_count == 1
    assert run.error_count == 1
    assert run.pass_count == 0

    result = (
        await db_session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    ).one()
    assert result.status == "error"
    assert "budget cap" in (result.error_message or "").lower()
    assert "blocked" in (result.error_message or "").lower()


async def test_run_eval_suite_case_is_blocked_by_a_team_cap_on_a_team_containing_the_agent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#354: an eval suite has no team context to pass to the budget
    check, but a team-scope hard_stop on a team the agent belongs to must
    still block it -- before this, the same agent was blocked in channel
    dispatch (which passes channel.team_id) yet ran freely from an eval
    case."""
    from rivulets.db.models import AgentRun, BudgetCap, Team, TeamAgent

    agent = await _make_agent(db_session, name="Spendy")
    team = Team(name="Capped Team")
    db_session.add(team)
    await db_session.flush()
    db_session.add(TeamAgent(team_id=team.id, agent_id=agent.id))
    db_session.add(
        BudgetCap(
            scope_type="team", team_id=team.id, period="day", limit_usd=0.5, action="hard_stop"
        )
    )
    # Already over the team's $0.50 cap before this run ever starts.
    db_session.add(AgentRun(agent_id=agent.id, model=agent.model, status="completed", cost_usd=1.0))
    suite = EvalSuite(name="s-team-budget", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "greets", "hi", "hello"))
    await db_session.commit()

    calls: list[str] = []

    async def counting_fake(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return await _fake_run_agent("hello")()

    monkeypatch.setattr("rivulets.evals.runner.run_agent", counting_fake)

    run = await run_eval_suite(db_session, suite, human_id=None)

    assert calls == []  # the model was never invoked
    assert run.error_count == 1

    result = (
        await db_session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    ).one()
    assert result.status == "error"
    assert "budget cap" in (result.error_message or "").lower()


async def test_run_eval_suite_structural_case_collects_tool_calls(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="s4", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(
        EvalCase(
            suite_id=suite.id,
            name="calls-search",
            input_content="find cats",
            judge_type="structural",
            expected_tool_name="search",
        )
    )
    await db_session.commit()

    fake_tool_call = SimpleNamespace(tool_name="search", tool_args={"query": "cats"})
    monkeypatch.setattr(
        "rivulets.evals.runner.run_agent", _fake_run_agent("done", tools=[fake_tool_call])
    )

    run = await run_eval_suite(db_session, suite, human_id=None)
    assert run.pass_count == 1

    result = (
        await db_session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    ).one()
    assert result.actual_tool_calls_json is not None
    assert "search" in result.actual_tool_calls_json


async def test_run_eval_suite_workflow_suite_creates_scratch_channel_once(
    db_session: AsyncSession,
) -> None:
    workflow = await _make_workflow(db_session, "greet-flow")
    node = WorkflowNode(
        workflow_id=workflow.id,
        name="shout",
        node_type="transform",
        config_json='{"template": "{input}!!!"}',
    )
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )

    suite = EvalSuite(name="s5", workflow_id=workflow.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "case-1", "hi", "hi!!!"))
    db_session.add(_exact_case(suite.id, "case-2", "yo", "yo!!!"))
    await db_session.commit()

    run = await run_eval_suite(db_session, suite, human_id=None)

    assert run.case_count == 2
    assert run.pass_count == 2

    channels = (await db_session.scalars(select(Channel).where(Channel.name == "_evals"))).all()
    assert len(channels) == 1

    rivulets = (
        await db_session.scalars(select(Rivulet).where(Rivulet.channel_id == channels[0].id))
    ).all()
    assert len(rivulets) == 2  # one fresh Rivulet per case


async def test_run_eval_suite_workflow_suite_case_error_on_pause(db_session: AsyncSession) -> None:
    """A workflow that pauses on a human_input node isn't supported by
    eval runs in v1 -- it's captured as that case's own execution error,
    not a hang."""
    workflow = await _make_workflow(db_session, "pausing-flow")
    node = WorkflowNode(workflow_id=workflow.id, name="ask", node_type="human_input")
    db_session.add(node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id)
    )

    suite = EvalSuite(name="s6", workflow_id=workflow.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(_exact_case(suite.id, "case-1", "hi", "hi"))
    await db_session.commit()

    run = await run_eval_suite(db_session, suite, human_id=None)
    assert run.error_count == 1

    result = (
        await db_session.scalars(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id))
    ).one()
    assert "human_input" in (result.error_message or "")


async def test_eval_triggered_workflow_failure_does_not_notify_on_call_or_remediate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the workflows/engine.py `_finalize_run` guard
    (#95): an eval suite deliberately exercising a failure case must not
    fire a production remediation workflow or @mention a real on-call
    agent -- both would otherwise happen for any other triggered_by value
    whose run fails (see test_workflow_engine.py's own remediation/on-call
    tests for the non-eval baseline this diverges from)."""
    from rivulets.workflows.engine import run_workflow

    oncall = await _make_agent(db_session, "OnCall")
    fixer = await _make_workflow(db_session, "fixer-eval")
    fixer_node = WorkflowNode(
        workflow_id=fixer.id,
        name="recover",
        node_type="transform",
        config_json='{"template": "fixed: {input}"}',
    )
    db_session.add(fixer_node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=fixer.id, from_node_id=None, to_node_id=fixer_node.id)
    )

    doomed = await _make_workflow(db_session, "doomed-eval")
    doomed.on_failure_workflow_id = fixer.id
    doomed.on_call_agent_id = oncall.id
    doomed_node = WorkflowNode(
        workflow_id=doomed.id, name="doomed", node_type="agent", agent_id=oncall.id
    )
    db_session.add(doomed_node)
    await db_session.flush()
    db_session.add(
        WorkflowConnection(workflow_id=doomed.id, from_node_id=None, to_node_id=doomed_node.id)
    )

    channel = Channel(name="eval-guard-channel")
    db_session.add(channel)
    await db_session.flush()
    rivulet = Rivulet(channel_id=channel.id, created_by="eval")
    db_session.add(rivulet)
    await db_session.commit()

    async def always_fails(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", always_fails)
    run = await run_workflow(
        db_session, doomed, rivulet, "hi", triggered_by="eval", triggered_by_id="eval-run-1"
    )

    assert run.status == "failed"

    remediation_runs = (
        await db_session.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == fixer.id))
    ).all()
    assert remediation_runs == []

    alerts = (
        await db_session.scalars(
            select(Message).where(
                Message.rivulet_id == rivulet.id, Message.content_type == "system_alert"
            )
        )
    ).all()
    assert not any("@" in m.content and oncall.name in m.content for m in alerts)
