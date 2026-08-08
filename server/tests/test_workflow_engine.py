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
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import (
    Channel,
    Message,
    Rivulet,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowNodeRun,
)
from rivulets.workflows.engine import MAX_NODE_VISITS_PER_RUN, run_workflow


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
