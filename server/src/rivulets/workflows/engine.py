"""Workflow execution engine (#24): runs a saved `Workflow` definition
end-to-end against a rivulet, one node at a time, following
`WorkflowConnection` edges.

Linear-only for this slice — every node the engine walks past has at most
one outbound connection, enforced by api/workflows.py at connection-create
time, not by this module. The engine itself doesn't assume linearity
beyond "follow the first outbound edge found"; a future branching engine
picks among multiple outbound edges (via `WorkflowConnection.condition_json`)
without changing anything below except that one lookup.

Mirrors dispatch/service.py's shape deliberately: `WorkflowRun`/
`WorkflowNodeRun` play the same "local execution telemetry" role as
DispatchDecision/AgentRun, and node output is posted into the rivulet as
Message rows the same way an agent's reply is, so a workflow run reads
like a sequence of chat messages, not a separate UI surface. A
`workflow_step` divider message (content_type='workflow_step') announces
each node the same way FR-6.3's handoff divider does for handoffs — the
engine's answer to issue #24's "does each node's execution post a visible
step indicator" open question, picked as the more transparent default;
nothing here forecloses making that configurable later.

Failure handling (#24's "Robustness" section): a node that exhausts its
retry budget stops the whole run and posts a system_alert — control goes
back to the human, no automatic escalation. A 'conditional' node whose
predicate doesn't match ends the run early too, but as a normal
completion (workflows/nodes.py's ConditionNotMetError), not a failure —
the workflow was designed to stop there.
"""

import asyncio
import json
import logging

from sqlalchemy import select
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


def _next_node_id(connections: list[WorkflowConnection], from_node_id: str | None) -> str | None:
    for connection in connections:
        if connection.from_node_id == from_node_id:
            return connection.to_node_id
    return None


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
    the first node's input. `triggered_by`/`triggered_by_id` record who
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

    first_node_id = _next_node_id(connections, None)
    if first_node_id is None or first_node_id not in nodes:
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

    current_node_id: str | None = first_node_id
    current_input = input_content

    while current_node_id is not None:
        node = nodes[current_node_id]
        run.current_node_id = node.id
        await db.commit()
        await _post_message(db, _step_message(rivulet, workflow, node))

        output, failure = await _run_node_with_retries(
            db, run, node, rivulet.agentos_session_id, current_input
        )

        if isinstance(failure, ConditionNotMetError):
            run.status = "completed"
            run.completed_at = utcnow_iso()
            await db.commit()
            return run

        if failure is not None:
            run.status = "failed"
            run.error_message = str(failure)
            run.completed_at = utcnow_iso()
            await db.commit()
            await _post_message(
                db,
                Message(
                    rivulet_id=rivulet.id,
                    sender_type="system",
                    sender_name="system",
                    content=f"Workflow /{workflow.name} stopped at step {node.name!r} — {failure}",
                    content_type="system_alert",
                ),
            )
            publish(
                rivulet.id,
                "system_alert",
                {"type": "workflow_failed", "workflow_id": workflow.id, "node_id": node.id},
            )
            return run

        assert output is not None
        await _post_message(db, _output_message(rivulet, node, output))

        current_input = output
        current_node_id = _next_node_id(connections, node.id)

    run.status = "completed"
    run.completed_at = utcnow_iso()
    await db.commit()
    return run


async def _run_node_with_retries(
    db: AsyncSession,
    run: WorkflowRun,
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
            workflow_run_id=run.id, node_id=node.id, attempt=attempt, input_content=input_content
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
