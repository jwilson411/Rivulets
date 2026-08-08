"""Workflow execution engine (#24, branching/parallel/loops via #81, real
merge joining via #82, pause/resume via #83): runs a saved `Workflow`
definition end-to-end against a rivulet, following `WorkflowConnection`
edges.

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

Merge nodes (#82): a branch that's about to advance onto a 'merge' node
stops there instead (`_advance`) and reports the arrival rather than
executing it, and single-edge continuations bubble that arrival straight
back up unresolved (`_follow_edges`) rather than firing the merge node
immediately. Resolution only happens where genuine concurrency actually
exists: at the nearest ancestor fan-out (2+ matching edges gathered
together) or, failing that, at run_workflow's own top level. That's what
lets siblings a *different* number of hops from the merge node (one path
runs through an extra node, another goes straight there) still join into
one execution — since a workflow has exactly one entry point, any two
branches that both reach the same merge node necessarily share some
common ancestor fan-out somewhere back up the graph; the join naturally
happens at whichever one is nearest. `_resolve_merge_arrivals` groups
whatever arrivals surface at that point by merge node id and runs each
group's merge node exactly once with all of their outputs combined
(`workflows/nodes.py`'s `execute_merge_node`), then continues the walk
from there. The one way a merge node still runs more than once for what
looks like "the same" arrival is a loop: a second pass through an
already-resolved fan-out is a temporally separate visit, not a sibling of
the first. If a sibling in a group fails outright, the whole run fails as
usual (see above) and the merge waiting on it never runs; a sibling that
instead dead-ends cleanly (an unmatched edge condition, or a conditional
node's own gate) simply doesn't contribute — the merge still fires with
however many of its group did arrive.

Pause/resume (#83): a branch about to advance onto a 'human_input' node
stops there too (`_advance` → `_pause_for_human_input`), the same
interception point as a merge arrival, but there's nothing to later
group -- it commits WorkflowRun.status='awaiting_human',
current_node_id=that node, and Rivulet.status='paused' (mirroring
dispatch/guards.py's loop-guard pause exactly, so the channel UI's
existing paused banner needs no changes to pick this up) and stops.
`resume_workflow` is the second, separate entry point api/rivulets.py
calls once workflows/trigger.py's find_awaiting_workflow_run recognizes
the next message in that rivulet as the reply (not run_workflow again --
there's no fresh input_content, no new entry point, just picking the walk
back up at `current_node_id` with the reply as that node's output). The
original call's visit_counts/total_steps were local Python state, not
persisted, so a resumed run's loop guard starts fresh. A pause inside a
fan-out that also shares a merge point with sibling branches has one
known gap: only the paused branch's own path is resumed -- a sibling that
already reached the merge node before the pause isn't replayed, so that
merge (if reached again on resume) fires with just the resumed branch's
input. Pausing on a linear path, or in a fan-out with no shared merge
downstream, has no such gap.
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
    # Set instead of `failed` when this branch's next hop is a 'merge' node:
    # (merge_node_id, this_branch's_output) — the branch stops there rather
    # than executing the merge node itself; see _resolve_merge_arrivals.
    arrived_at_merge: tuple[str, str] | None = None
    # Set when this branch stopped at a 'human_input' node (#83) rather
    # than finishing or failing — see _pause_for_human_input. Precedence
    # in _resolve_merge_arrivals/run_workflow is failed > paused >
    # completed: a hard error elsewhere in the run matters more than a
    # pause, but a pause still needs to win over sibling branches that
    # happened to complete cleanly.
    paused: bool = False


def _check_loop_guard(
    node: WorkflowNode, visit_counts: dict[str, int], total_steps: list[int]
) -> str | None:
    """Shared by every node executor (_run_branch, _run_merge_node,
    _pause_for_human_input): returns a detail message once `node` (or the
    run overall) has been visited too many times, else None. See module
    docstring's "Loops" section."""
    visit_counts[node.id] = visit_counts.get(node.id, 0) + 1
    total_steps[0] += 1
    if visit_counts[node.id] > MAX_NODE_VISITS_PER_RUN or total_steps[0] > MAX_TOTAL_STEPS_PER_RUN:
        return (
            f"exceeded the workflow's loop guard ({MAX_NODE_VISITS_PER_RUN} visits to this step "
            f"or {MAX_TOTAL_STEPS_PER_RUN} steps in this run) — likely an unbounded loop"
        )
    return None


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
        # _run_merge_node is the only caller that ever reaches a merge node
        # (see module docstring / _resolve_merge_arrivals) -- it always
        # passes a JSON-encoded list, never a bare string.
        return execute_merge_node(node, json.loads(input_content))
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

    visit_counts: dict[str, int] = {}
    total_steps = [0]
    entry_outcome = await _advance(
        db,
        run.id,
        workflow,
        rivulet.id,
        nodes,
        connections,
        entry_node_id,
        input_content,
        visit_counts,
        total_steps,
    )
    outcome = await _resolve_merge_arrivals(
        [entry_outcome],
        db,
        run.id,
        workflow,
        rivulet.id,
        nodes,
        connections,
        visit_counts,
        total_steps,
    )

    return await _finalize_run(db, run, outcome)


async def _finalize_run(db: AsyncSession, run: WorkflowRun, outcome: _BranchOutcome) -> WorkflowRun:
    """Shared by run_workflow and resume_workflow. A paused outcome's
    status/current_node_id were already committed inside
    _pause_for_human_input, possibly on a different (sibling) session --
    refresh rather than reassign so the returned `run` reflects that write
    instead of clobbering it with a stale in-memory value."""
    if outcome.paused:
        await db.refresh(run)
        return run

    run.status = "failed" if outcome.failed else "completed"
    run.error_message = outcome.error_message
    run.completed_at = utcnow_iso()
    await db.commit()
    return run


async def resume_workflow(db: AsyncSession, run: WorkflowRun, human_reply: str) -> WorkflowRun:
    """Continues a WorkflowRun paused by a 'human_input' node (#83, see
    _pause_for_human_input), called from api/rivulets.py's post_message
    once workflows/trigger.py's find_awaiting_workflow_run identifies the
    next message in that rivulet as the reply. `human_reply` becomes the
    paused node's output — the same "a node's output becomes the next
    node's input" contract every other node type honors (issue #24) — and
    the walk continues from there via _follow_edges. The loop guard
    starts fresh: visit_counts/total_steps were local to the original
    run_workflow call and aren't persisted across a pause, so a run that
    paused near its budget gets a clean one again on resume rather than
    inheriting an exhausted one."""
    assert run.current_node_id is not None  # set by _pause_for_human_input
    workflow = await db.get(Workflow, run.workflow_id)
    rivulet = await db.get(Rivulet, run.rivulet_id)
    assert workflow is not None
    assert rivulet is not None

    nodes, connections = await _load_nodes_and_connections(db, workflow.id)
    paused_node = nodes.get(run.current_node_id)
    if paused_node is None:
        return await _finalize_run(
            db,
            run,
            _BranchOutcome(
                failed=True, error_message="The paused step was deleted before you replied"
            ),
        )

    paused_node_run = await db.scalar(
        select(WorkflowNodeRun)
        .where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.node_id == paused_node.id,
            WorkflowNodeRun.status == "awaiting_human",
        )
        .order_by(WorkflowNodeRun.started_at.desc())
        .limit(1)
    )
    if paused_node_run is not None:
        paused_node_run.status = "completed"
        paused_node_run.output_content = human_reply
        paused_node_run.completed_at = utcnow_iso()

    rivulet.status = "active"
    run.status = "running"
    await db.commit()

    outcome = await _follow_edges(
        db,
        run.id,
        workflow,
        rivulet.id,
        nodes,
        connections,
        paused_node.id,
        human_reply,
        visit_counts={},
        total_steps=[0],
    )
    return await _finalize_run(db, run, outcome)


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

    detail = _check_loop_guard(node, visit_counts, total_steps)
    if detail is not None:
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

    return await _follow_edges(
        db,
        run_id,
        workflow,
        rivulet_id,
        nodes,
        connections,
        node.id,
        output,
        visit_counts,
        total_steps,
    )


async def _follow_edges(
    db: AsyncSession,
    run_id: str,
    workflow: Workflow,
    rivulet_id: str,
    nodes: dict[str, WorkflowNode],
    connections: list[WorkflowConnection],
    from_node_id: str,
    output: str,
    visit_counts: dict[str, int],
    total_steps: list[int],
) -> _BranchOutcome:
    """Shared tail for both _run_branch and _run_merge_node: pick
    `from_node_id`'s matching outbound edges, advance onto each (fanning
    out to fresh sessions beyond the first -- see module docstring).

    A single matching edge introduces no concurrency, so it isn't a real
    fan-out point -- an unresolved merge arrival is passed straight back
    to the caller rather than resolved here. That matters for siblings
    that reach the same merge node a different number of hops apart (one
    goes straight there, another passes through an extra node first): if
    this level resolved eagerly, the extra-hop sibling's arrival would
    fire the merge node alone before its sibling ever got a chance to
    join it. Resolution only happens where genuine concurrency exists
    (2+ matching edges, gathered below) or at run_workflow's top level,
    which is the guaranteed final catch-all for whatever's still
    unresolved once a branch stops introducing new fan-outs."""
    matching = _matching_outbound_edges(connections, from_node_id, output)
    if not matching:
        return _BranchOutcome(failed=False)  # terminal node, or no edge's condition matched

    if len(matching) == 1:
        return await _advance(
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
            return await _advance(
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
        _advance(
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
    return await _resolve_merge_arrivals(
        list(outcomes),
        db,
        run_id,
        workflow,
        rivulet_id,
        nodes,
        connections,
        visit_counts,
        total_steps,
    )


async def _advance(
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
    """The one place a branch is about to move onto a node: if that node
    is a 'merge', stop here and report the arrival instead of executing it
    (a merge node only ever runs via _run_merge_node, once per group of
    siblings -- see _resolve_merge_arrivals). If it's a 'human_input'
    node, pause instead (#83, _pause_for_human_input). Otherwise just
    _run_branch as normal."""
    target = nodes.get(node_id)
    if target is not None and target.node_type == "merge":
        return _BranchOutcome(failed=False, arrived_at_merge=(node_id, input_content))
    if target is not None and target.node_type == "human_input":
        return await _pause_for_human_input(
            db, run_id, workflow, rivulet_id, target, input_content, visit_counts, total_steps
        )
    return await _run_branch(
        db,
        run_id,
        workflow,
        rivulet_id,
        nodes,
        connections,
        node_id,
        input_content,
        visit_counts,
        total_steps,
    )


async def _pause_for_human_input(
    db: AsyncSession,
    run_id: str,
    workflow: Workflow,
    rivulet_id: str,
    node: WorkflowNode,
    input_content: str,
    visit_counts: dict[str, int],
    total_steps: list[int],
) -> _BranchOutcome:
    """#83: stops this branch at a 'human_input' node and waits. One
    WorkflowNodeRun row is created now (status='awaiting_human',
    output_content still unset) and *updated* in place by resume_workflow
    once a reply arrives, rather than each attempt getting its own row
    the way _run_node_with_retries does -- there's no attempt/retry
    concept for a pause, just one wait that eventually resolves.
    Rivulet.status='paused' + a system_alert mirrors dispatch/guards.py's
    loop-guard pause exactly, so the existing paused-rivulet banner in the
    channel UI picks this up with no UI changes needed."""
    detail = _check_loop_guard(node, visit_counts, total_steps)
    if detail is not None:
        await _post_alert(db, rivulet_id, workflow, node, detail)
        return _BranchOutcome(failed=True, error_message=detail, node_name=node.name)

    rivulet = await db.get(Rivulet, rivulet_id)
    assert rivulet is not None

    node_run = WorkflowNodeRun(
        workflow_run_id=run_id,
        node_id=node.id,
        attempt=1,
        input_content=input_content,
        status="awaiting_human",
    )
    db.add(node_run)
    await db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .values(current_node_id=node.id, status="awaiting_human")
    )
    rivulet.status = "paused"
    await db.commit()
    await _post_message(db, _step_message(rivulet, workflow, node))
    await _post_message(
        db,
        Message(
            rivulet_id=rivulet_id,
            sender_type="system",
            sender_name="system",
            content=f"⏸ Workflow /{workflow.name} is waiting for your reply at step {node.name!r}.",
            content_type="system_alert",
        ),
    )
    publish(
        rivulet_id,
        "system_alert",
        {"type": "workflow_awaiting_human", "workflow_id": workflow.id, "node_id": node.id},
    )
    return _BranchOutcome(failed=False, paused=True, node_name=node.name)


async def _resolve_merge_arrivals(
    outcomes: list[_BranchOutcome],
    db: AsyncSession,
    run_id: str,
    workflow: Workflow,
    rivulet_id: str,
    nodes: dict[str, WorkflowNode],
    connections: list[WorkflowConnection],
    visit_counts: dict[str, int],
    total_steps: list[int],
) -> _BranchOutcome:
    """`outcomes` is one fan-out's worth of sibling results (or a single
    result, for the non-branching case) -- this is the *only* place a
    merge node actually executes. Siblings that arrived at the *same*
    merge node are grouped and it runs once with all of their outputs
    (workflows/nodes.py's execute_merge_node); this only joins branches
    that diverged together at this fan-out, not any two branches anywhere
    in the graph that happen to reach the same merge node (see module
    docstring) -- reached some other way, a merge node still just runs
    once per independent arrival, unchanged from #81. Recurses because a
    merge node's own next hop could be another merge node.

    A paused (#83) sibling takes priority over any unresolved merge
    arrivals in the same batch (but not over an outright failure, checked
    first) -- if one sibling is waiting on a human, a merge that also
    needed *another* sibling's contribution can't fire yet either way, so
    there's nothing to lose by not attempting it. Resuming re-enters at
    the paused node specifically (resume_workflow), not this fan-out, so
    any sibling that already reached a merge node before the pause won't
    be replayed -- see module docstring's pause/resume section."""
    failed = next((o for o in outcomes if o.failed), None)
    if failed is not None:
        return failed

    paused = next((o for o in outcomes if o.paused), None)
    if paused is not None:
        return paused

    merge_groups: dict[str, list[str]] = {}
    for outcome in outcomes:
        if outcome.arrived_at_merge is not None:
            merge_id, merge_input = outcome.arrived_at_merge
            merge_groups.setdefault(merge_id, []).append(merge_input)

    if not merge_groups:
        return _BranchOutcome(failed=False)

    groups = list(merge_groups.items())

    async def _in_new_session(merge_id: str, inputs: list[str]) -> _BranchOutcome:
        async with session_scope() as branch_db:
            return await _run_merge_node(
                branch_db,
                run_id,
                workflow,
                rivulet_id,
                nodes,
                connections,
                merge_id,
                inputs,
                visit_counts,
                total_steps,
            )

    merge_outcomes = await asyncio.gather(
        _run_merge_node(
            db,
            run_id,
            workflow,
            rivulet_id,
            nodes,
            connections,
            groups[0][0],
            groups[0][1],
            visit_counts,
            total_steps,
        ),
        *(_in_new_session(merge_id, inputs) for merge_id, inputs in groups[1:]),
    )
    return await _resolve_merge_arrivals(
        list(merge_outcomes),
        db,
        run_id,
        workflow,
        rivulet_id,
        nodes,
        connections,
        visit_counts,
        total_steps,
    )


async def _run_merge_node(
    db: AsyncSession,
    run_id: str,
    workflow: Workflow,
    rivulet_id: str,
    nodes: dict[str, WorkflowNode],
    connections: list[WorkflowConnection],
    merge_node_id: str,
    inputs: list[str],
    visit_counts: dict[str, int],
    total_steps: list[int],
) -> _BranchOutcome:
    """Executes a merge node exactly once for the group of sibling inputs
    _resolve_merge_arrivals collected, then continues the walk from there
    -- the same visit/step accounting, message posting, and edge-following
    _run_branch does for every other node type, just with a pre-combined
    list of inputs instead of a single predecessor's output."""
    node = nodes.get(merge_node_id)
    if node is None:
        return _BranchOutcome(failed=False)

    detail = _check_loop_guard(node, visit_counts, total_steps)
    if detail is not None:
        await _post_alert(db, rivulet_id, workflow, node, detail)
        return _BranchOutcome(failed=True, error_message=detail, node_name=node.name)

    rivulet = await db.get(Rivulet, rivulet_id)
    assert rivulet is not None
    session_id = rivulet.agentos_session_id
    assert session_id is not None
    await db.execute(
        update(WorkflowRun).where(WorkflowRun.id == run_id).values(current_node_id=node.id)
    )
    await db.commit()
    await _post_message(db, _step_message(rivulet, workflow, node))

    output, failure = await _run_node_with_retries(db, run_id, node, session_id, json.dumps(inputs))

    if failure is not None:
        await _post_alert(db, rivulet_id, workflow, node, str(failure))
        return _BranchOutcome(failed=True, error_message=str(failure), node_name=node.name)

    assert output is not None
    await _post_message(db, _output_message(rivulet, node, output))

    return await _follow_edges(
        db,
        run_id,
        workflow,
        rivulet_id,
        nodes,
        connections,
        node.id,
        output,
        visit_counts,
        total_steps,
    )


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
