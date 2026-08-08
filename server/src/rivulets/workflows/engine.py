"""Workflow execution engine (#24, branching/parallel/loops via #81): runs
a saved `Workflow` definition end-to-end against a rivulet, following
`WorkflowConnection` edges.

A node may now have more than one outbound edge (api/workflows.py only
enforces a single *entry* connection, from_node_id=None, since a workflow
still starts at exactly one node). Each outbound edge's `condition_json`
decides whether it's followed: absent/null always matches; `{"contains":
"text"}` / `{"not_contains": "text"}` case-insensitively (sub)string-match
the source node's output — the same predicate shape `workflows/nodes.py`'s
'conditional' node already used, just movable onto an edge instead of only
living on the node. A node with several matching edges fans its output out
to all of them concurrently ("Parallel steps" in #81) rather than only
following the first. This is deliberately *not* how the 'conditional' node
type's own `config.contains` predicate + ConditionNotMetError works — that
single-edge "stop the whole run early" mechanism (workflows/nodes.py) is
unchanged, since it's a genuinely different, still-useful shape (a solo
gate rather than a labeled branch); a workflow author wanting real
if/else branching leaves a conditional node's own config empty and puts
complementary contains/not_contains conditions on its two outbound edges
instead.

Each fan-out branch beyond the first runs its own recursive walk on a
*fresh* `AsyncSession` (db/session.py's `session_scope`, the same
"open my own session, this isn't running inside the caller's request"
pattern dispatch/service.py's invoke_agent_remotely uses) — a single
AsyncSession can't safely be driven by more than one coroutine at once.
The first matching edge keeps reusing the caller's session (no new
session/transaction for the common single-branch case, so an ordinary
linear workflow behaves exactly as before). `nodes`/`connections`/
`workflow`, loaded once up front, are read-only for the rest of a run and
safe to share read-only attribute access across those sibling sessions
(the session factory is `expire_on_commit=False`, so committed objects
don't try to refresh themselves against whichever session loaded them).

Loops fall out of the same mechanism: nothing stops an edge from pointing
back at an already-visited node, so a cycle in the graph just means a
branch revisits a node. Two cheap, deliberately unconfigurable-for-now
guards (mirroring dispatch/guards.py's cycle detection for agent-to-agent
chat, the same "unbounded loop is a runaway-cost failure mode" concern)
cap that: MAX_NODE_VISITS_PER_RUN per node, MAX_TOTAL_STEPS_PER_RUN across
the whole run (protects against wide fan-out amplifying a smaller loop).
Tripping either ends the run the same way a node failure does.

Mirrors dispatch/service.py's shape deliberately: `WorkflowRun`/
`WorkflowNodeRun` play the same "local execution telemetry" role as
DispatchDecision/AgentRun, and node output is posted into the rivulet as
Message rows the same way an agent's reply is, so a workflow run reads
like a sequence of chat messages, not a separate UI surface. A
`workflow_step` divider message (content_type='workflow_step') announces
each node the same way FR-6.3's handoff divider does for handoffs.

Failure handling (#24's "Robustness" section): a node that exhausts its
retry budget stops its branch and posts a system_alert — control goes
back to the human, no automatic escalation. If any branch of a run fails
(or trips a loop guard), the whole WorkflowRun is marked 'failed' with
that branch's error, even if sibling branches completed cleanly — a
partially-successful fan-out is still a run a human should look at.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import (
    Message,
    Rivulet,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from rivulets.db.session import session_scope
from rivulets.streaming import publish
from rivulets.sync.publish import publish_current_state
from rivulets.workflows.nodes import (
    ConditionNotMetError,
    execute_agent_node,
    execute_conditional_node,
    execute_merge_node,
    execute_summarize_node,
    execute_transform_node,
)

logger = logging.getLogger(__name__)

# Loop guard (#81's "bounded iteration construct"): not a WorkspaceSetting
# like dispatch/guards.py's thresholds (FR-7.4 needed those configurable;
# nothing here has asked for that yet) -- just two fixed caps, generous
# enough for legitimate bounded loops, cheap insurance against a mis-wired
# one running away.
MAX_NODE_VISITS_PER_RUN = 25
MAX_TOTAL_STEPS_PER_RUN = 200


@dataclass
class _BranchOutcome:
    failed: bool
    error_message: str | None = None
    node_name: str | None = None


async def _post_message(db: AsyncSession, message: Message) -> Message:
    """Persists and syncs one workflow-produced message. Unlike
    dispatch/service.py's messages (returned up to api/rivulets.py's
    request handler, which commits/publishes them together), this engine
    has no such outer handler watching its output — it owns its own
    commit/publish per message, the same self-contained pattern
    dispatch/service.py's invoke_agent_remotely uses when there's no
    request in flight to do it instead."""
    db.add(message)
    await db.commit()
    await db.refresh(message)
    await publish_current_state(db, "message", message.id)
    return message


async def _post_alert(
    db: AsyncSession, rivulet_id: str, workflow: Workflow, node: WorkflowNode, detail: str
) -> None:
    await _post_message(
        db,
        Message(
            rivulet_id=rivulet_id,
            sender_type="system",
            sender_name="system",
            content=f"Workflow /{workflow.name} stopped at step {node.name!r} — {detail}",
            content_type="system_alert",
        ),
    )
    publish(
        rivulet_id,
        "system_alert",
        {"type": "workflow_failed", "workflow_id": workflow.id, "node_id": node.id},
    )


async def _load_nodes_and_connections(
    db: AsyncSession, workflow_id: str
) -> tuple[dict[str, WorkflowNode], list[WorkflowConnection]]:
    nodes_result = await db.execute(
        select(WorkflowNode).where(WorkflowNode.workflow_id == workflow_id)
    )
    nodes = {node.id: node for node in nodes_result.scalars().all()}
    connections_result = await db.execute(
        select(WorkflowConnection)
        .where(WorkflowConnection.workflow_id == workflow_id)
        .order_by(WorkflowConnection.created_at)
    )
    connections = list(connections_result.scalars().all())
    return nodes, connections


def _entry_node_id(connections: list[WorkflowConnection]) -> str | None:
    for connection in connections:
        if connection.from_node_id is None:
            return connection.to_node_id
    return None


def _parse_condition(condition_json: str) -> dict[str, object] | None:
    try:
        condition: object = json.loads(condition_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(condition, dict):
        return None
    return cast(dict[str, object], condition)


def _edge_matches(edge: WorkflowConnection, output_content: str) -> bool:
    if not edge.condition_json:
        return True
    condition = _parse_condition(edge.condition_json)
    if condition is None:
        return True
    if "contains" in condition:
        needle = condition["contains"]
        return isinstance(needle, str) and needle.lower() in output_content.lower()
    if "not_contains" in condition:
        needle = condition["not_contains"]
        return not (isinstance(needle, str) and needle.lower() in output_content.lower())
    return True


def _matching_outbound_edges(
    connections: list[WorkflowConnection], from_node_id: str, output_content: str
) -> list[WorkflowConnection]:
    return [
        c
        for c in connections
        if c.from_node_id == from_node_id and _edge_matches(c, output_content)
    ]


def _step_message(rivulet: Rivulet, workflow: Workflow, node: WorkflowNode) -> Message:
    publish(
        rivulet.id,
        "workflow_step",
        {"workflow_id": workflow.id, "node_id": node.id, "node_name": node.name},
    )
    return Message(
        rivulet_id=rivulet.id,
        sender_type="system",
        sender_name="system",
        content=f"▶ Workflow /{workflow.name} → {node.name}",
        content_type="workflow_step",
    )


def _output_message(rivulet: Rivulet, node: WorkflowNode, content: str) -> Message:
    return Message(
        rivulet_id=rivulet.id,
        sender_type="agent" if node.node_type == "agent" else "system",
        sender_id=node.agent_id if node.node_type == "agent" else None,
        sender_name=node.name,
        content=content,
        metadata_json=json.dumps({"workflow_node_id": node.id}),
    )


async def _execute_node(
    db: AsyncSession, node: WorkflowNode, session_id: str, input_content: str
) -> str:
    if node.node_type == "agent":
        return await execute_agent_node(db, node, session_id, input_content)
    if node.node_type == "transform":
        return execute_transform_node(node, input_content)
    if node.node_type == "summarize":
        return await execute_summarize_node(db, node, input_content)
    if node.node_type == "conditional":
        return execute_conditional_node(node, input_content)
    if node.node_type == "merge":
        return execute_merge_node(input_content)
    raise ValueError(f"Unknown node_type {node.node_type!r}")


async def run_workflow(
    db: AsyncSession,
    workflow: Workflow,
    rivulet: Rivulet,
    input_content: str,
    *,
    triggered_by: str,
    triggered_by_id: str | None,
) -> WorkflowRun:
    """Run `workflow` against `rivulet`, starting with `input_content` as
    the entry node's input. `triggered_by`/`triggered_by_id` record who
    kicked this off ('human' from api/rivulets.py's slash-command
    interceptor, or 'agent' from tools/builtin/run_workflow.py) for the
    same audit purpose AgentRun.source does for dispatcher-originated LLM
    calls. Returns the WorkflowRun row (already committed) regardless of
    outcome — callers inspect `.status` rather than this raising, mirroring
    dispatch_and_respond's "failures are recorded, not propagated" style.
    """
    nodes, connections = await _load_nodes_and_connections(db, workflow.id)

    run = WorkflowRun(
        workflow_id=workflow.id,
        rivulet_id=rivulet.id,
        triggered_by=triggered_by,
        triggered_by_id=triggered_by_id,
        input_content=input_content,
    )
    db.add(run)
    await db.flush()

    if rivulet.agentos_session_id is None:
        rivulet.agentos_session_id = rivulet.id  # FR-12.2: one AgentOS session per rivulet

    entry_node_id = _entry_node_id(connections)
    if entry_node_id is None or entry_node_id not in nodes:
        run.status = "failed"
        run.error_message = "Workflow has no entry point"
        run.completed_at = utcnow_iso()
        await db.commit()
        await _post_message(
            db,
            Message(
                rivulet_id=rivulet.id,
                sender_type="system",
                sender_name="system",
                content=f"Workflow /{workflow.name} can't run — it has no starting node.",
                content_type="system_alert",
            ),
        )
        return run

    # Commit now (run row + agentos_session_id) so any sibling sessions a
    # fan-out opens below can see this run and rivulet immediately.
    await db.commit()

    outcome = await _run_branch(
        db,
        run.id,
        workflow,
        rivulet.id,
        nodes,
        connections,
        entry_node_id,
        input_content,
        visit_counts={},
        total_steps=[0],
    )

    run.status = "failed" if outcome.failed else "completed"
    run.error_message = outcome.error_message
    run.completed_at = utcnow_iso()
    await db.commit()
    return run


async def _run_branch(
    db: AsyncSession,
    run_id: str,
    workflow: Workflow,
    rivulet_id: str,
    nodes: dict[str, WorkflowNode],
    connections: list[WorkflowConnection],
    node_id: str,
    input_content: str,
    visit_counts: dict[str, int],
    total_steps: list[int],
) -> _BranchOutcome:
    """Executes `node_id` and everything reachable from it on this branch,
    recursing (or fanning out — see module docstring) for each matching
    outbound edge. `visit_counts`/`total_steps` are shared, plain mutable
    containers across every branch of one run, including ones on sibling
    sessions — safe without a lock because asyncio only ever runs one
    coroutine's synchronous code at a time; each check-then-increment here
    completes before the next `await`."""
    node = nodes.get(node_id)
    if node is None:
        return _BranchOutcome(failed=False)  # edge to a since-deleted node: a quiet dead end

    visit_counts[node_id] = visit_counts.get(node_id, 0) + 1
    total_steps[0] += 1
    if visit_counts[node_id] > MAX_NODE_VISITS_PER_RUN or total_steps[0] > MAX_TOTAL_STEPS_PER_RUN:
        detail = (
            f"exceeded the workflow's loop guard ({MAX_NODE_VISITS_PER_RUN} visits to this step "
            f"or {MAX_TOTAL_STEPS_PER_RUN} steps in this run) — likely an unbounded loop"
        )
        await _post_alert(db, rivulet_id, workflow, node, detail)
        return _BranchOutcome(failed=True, error_message=detail, node_name=node.name)

    rivulet = await db.get(Rivulet, rivulet_id)
    assert rivulet is not None
    session_id = rivulet.agentos_session_id
    assert session_id is not None  # run_workflow sets this before any branch starts
    await db.execute(
        update(WorkflowRun).where(WorkflowRun.id == run_id).values(current_node_id=node.id)
    )
    await db.commit()
    await _post_message(db, _step_message(rivulet, workflow, node))

    output, failure = await _run_node_with_retries(db, run_id, node, session_id, input_content)

    if isinstance(failure, ConditionNotMetError):
        return _BranchOutcome(failed=False)  # the node's own gate said stop here, not a failure

    if failure is not None:
        await _post_alert(db, rivulet_id, workflow, node, str(failure))
        return _BranchOutcome(failed=True, error_message=str(failure), node_name=node.name)

    assert output is not None
    await _post_message(db, _output_message(rivulet, node, output))

    matching = _matching_outbound_edges(connections, node.id, output)
    if not matching:
        return _BranchOutcome(failed=False)  # terminal node, or no edge's condition matched

    if len(matching) == 1:
        return await _run_branch(
            db,
            run_id,
            workflow,
            rivulet_id,
            nodes,
            connections,
            matching[0].to_node_id,
            output,
            visit_counts,
            total_steps,
        )

    async def _in_new_session(to_node_id: str) -> _BranchOutcome:
        async with session_scope() as branch_db:
            return await _run_branch(
                branch_db,
                run_id,
                workflow,
                rivulet_id,
                nodes,
                connections,
                to_node_id,
                output,
                visit_counts,
                total_steps,
            )

    outcomes = await asyncio.gather(
        _run_branch(
            db,
            run_id,
            workflow,
            rivulet_id,
            nodes,
            connections,
            matching[0].to_node_id,
            output,
            visit_counts,
            total_steps,
        ),
        *(_in_new_session(edge.to_node_id) for edge in matching[1:]),
    )
    return next((o for o in outcomes if o.failed), _BranchOutcome(failed=False))


async def _run_node_with_retries(
    db: AsyncSession,
    run_id: str,
    node: WorkflowNode,
    session_id: str,
    input_content: str,
) -> tuple[str | None, Exception | None]:
    """Executes `node`, retrying up to `node.retry_max_attempts` additional
    times on failure with `node.retry_backoff_seconds` between attempts —
    one WorkflowNodeRun row per attempt (db/models.py's docstring on why:
    keeps retry history inspectable). A ConditionNotMetError is never
    retried — it isn't a transient failure, it's the node behaving exactly
    as configured."""
    max_attempts = max(1, node.retry_max_attempts + 1)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        node_run = WorkflowNodeRun(
            workflow_run_id=run_id, node_id=node.id, attempt=attempt, input_content=input_content
        )
        db.add(node_run)
        await db.flush()
        try:
            output = await _execute_node(db, node, session_id, input_content)
        except ConditionNotMetError as exc:
            node_run.status = "skipped"
            node_run.error_message = str(exc)
            node_run.completed_at = utcnow_iso()
            await db.commit()
            return None, exc
        except Exception as exc:  # noqa: BLE001 — any node failure is retried/reported uniformly
            last_error = exc
            node_run.status = "failed"
            node_run.error_message = str(exc)
            node_run.completed_at = utcnow_iso()
            await db.commit()
            logger.warning(
                "Workflow node %r (attempt %d/%d) failed",
                node.name,
                attempt,
                max_attempts,
                exc_info=True,
            )
            if attempt < max_attempts:
                await asyncio.sleep(node.retry_backoff_seconds)
            continue
        else:
            node_run.status = "completed"
            node_run.output_content = output
            node_run.completed_at = utcnow_iso()
            await db.commit()
            return output, None

    return None, last_error
