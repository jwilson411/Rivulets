"""Workflow execution engine (#24, branching/parallel/loops via #81, real
merge joining via #82, pause/resume via #83, run-graph snapshotting via
#84, nested workflows via #85): runs a saved `Workflow` definition
end-to-end against a rivulet, following `WorkflowConnection` edges.

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
Tripping either ends the run the same way a node failure does. Both live
on a shared, per-run `_RunContext` (see below) alongside the ancestry set
#85's cycle guard needs.

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
"No automatic escalation" gained two narrow, opt-in, independently
configurable exceptions: #94 layer 2's `_maybe_trigger_remediation`
(`Workflow.on_failure_workflow_id`, a fresh top-level run_workflow call —
see that function's own docstring for the depth-1 cap that keeps it from
looping) and #94 layer 3's `_maybe_notify_on_call_agent`
(`Workflow.on_call_agent_id` / the workspace-wide default, `@mention`ed
via the ordinary dispatch path). `_finalize_run` calls both on a failed
run; neither depends on or excludes the other.

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
back up at `current_node_id` with the reply as that node's output).
Pausing inside a fan-out (#359): a sibling branch that reached a shared
merge node before the pause has its arrival banked on
`WorkflowRun.pending_merge_arrivals_json` rather than dropped, and
resume_workflow folds those back into merge resolution once every wait
is answered -- the merge fires with every input that actually reached
it, not just the resumed branch's. Sibling branches that pause in
parallel resolve one reply at a time: resume completes the wait at
`current_node_id`, and if another branch's WorkflowNodeRun is still
'awaiting_human' the run re-pauses on it (_repause_on_waiting_branch)
instead of finalizing. A sibling that fails outright still fails the
whole run (failed > paused, unchanged), but _finalize_run now also fails
any dangling 'awaiting_human' node runs and releases the rivulet's
pause, so a failed run can't leave the paused banner up with no
resumable run underneath it.

Run-graph snapshotting (#84): `run_workflow` freezes the workflow's nodes
and connections into `WorkflowRun.graph_snapshot_json`
(`_serialize_graph`) the moment a run starts, and `resume_workflow` reads
that back (`_deserialize_graph`) instead of re-querying the live
WorkflowNode/WorkflowConnection rows -- a workflow edited in the builder
while a run sits paused on a 'human_input' node doesn't change what that
paused run does on resume. A run that never crosses a pause boundary
doesn't strictly need this (the graph is already loaded once into local
variables for that whole synchronous call), but every run gets a snapshot
uniformly. `Workflow.published` is a separate, narrower gate on top of
this: it only controls whether a *new* run can be *started* via the
slash-command/run_workflow-tool triggers (workflows/trigger.py's
find_workflow_by_name) -- it isn't a second, independent copy of the
graph the way a full draft/published model would be, so editing an
already-published workflow still takes effect immediately for the next
trigger. See Workflow's own docstring for why that's the scope this
landed with.

Nested workflows (#85): a 'workflow' node (WorkflowNode.child_workflow_id)
is a normal node type again, unlike merge/human_input -- it runs
synchronously to completion via `_execute_workflow_node`
(workflows/nodes.py doesn't define it; see that module's docstring for
why), dispatched from `_execute_node` and retried like any other node.
It recursively calls `run_workflow` with this node's input as the child's
`input_content`, `triggered_by="workflow"`/`triggered_by_id=`this run's
id, and the *current* run's ancestry (the set of workflow ids already
executing up this call chain, including this workflow itself) so the
child's own `run_workflow` call can refuse to start if its target is
already in that set -- a structural cycle guard, checked live rather than
at save time (the same tradeoff Workflow's docstring already made for
other structural invariants). A completed child's `WorkflowRun.
final_output` becomes this node's own output, the same "a node's output
becomes the next node's input" contract every other type honors -- which
is also why `_BranchOutcome` now carries an `output` field threaded
through every clean-success return, not just failure/pause: nothing else
needed a queryable "this run's result" before nesting required one.

Two more guards on top of the cycle check (#249): `MAX_WORKFLOW_NESTING_
DEPTH` caps how deep an *acyclic* chain of distinct nested workflows can
go (the ancestry set's cycle guard alone doesn't bound that), and
`_execute_workflow_node` re-checks `child_workflow.published` immediately
before invoking it -- the same gate every other trigger path
(workflows/trigger.py's `find_workflow_by_name`, api/webhooks.py,
workflows/scheduler.py) already applies. Before #249, neither existed: a
'workflow' node could nest-run an unpublished child (locally, or --
because `WORKFLOW_NODE_SPEC`/`WORKFLOW_CONNECTION_SPEC` sync a workflow's
graph independently of its `published` flag -- a child synced from a peer
that had fully built but not yet published it), and a long non-cyclic
nesting chain had no depth limit at all, only the loop guard's total-step
budget to eventually exhaust.

Deliberately out of scope: a nested child pausing on its own 'human_input'
node. `_execute_workflow_node` treats that outcome as a failure of the
parent's 'workflow' node rather than propagating the pause up through
however many ancestor runs are waiting -- correctly resuming an entire
nested chain (which run does a reply belong to? does every ancestor also
need graph_snapshot_json-style protection against edits made while
*it* was blocked on a grandchild?) is a substantial, separate problem
issue #85 doesn't ask this engine to solve, and #83 (which introduced
pausing) predates nesting entirely. `_execute_workflow_node` fails the
child run too when this happens (not just the parent) and clears the
rivulet's pause, rather than leaving a paused-but-orphaned child sitting
there discoverable by a later reply with no parent left waiting on it.
Since #359 this is a backstop rather than the primary defense:
api/workflows.py refuses at save time to wire a 'workflow' node whose
child (transitively) contains a 'human_input' node, and to add a
'human_input' node to a workflow already embedded as a child -- only
graphs that arrive by sync (which bypasses API validation) can still
reach this runtime path.
Because of this, `resume_workflow` never needs nesting-awareness: a
nested run either completes or fails synchronously within the same
`run_workflow` call that started it, so `find_awaiting_workflow_run` can
only ever find a top-level (triggered_by != 'workflow') run.

Run history (#85's other open question) is deliberately flat, not a
cross-workflow tree: a nested child's own WorkflowRun rows only show up
under *its own* workflow's run history (`GET /workflows/{id}/runs`), not
folded into the parent's. `triggered_by`/`triggered_by_id` are what let a
human or tool trace a nested run back to the parent node that started it,
without this module or its API needing a new tree-shaped endpoint.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import (
    Agent,
    Channel,
    Message,
    Rivulet,
    RunSpan,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkspaceSetting,
)
from rivulets.db.session import session_scope
from rivulets.streaming import publish
from rivulets.sync.publish import publish_current_state
from rivulets.tracing import TraceContext, finish_span, start_span
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

# #249: a structural cap on how many workflows can be nested inside each
# other (len(ctx.ancestry) once a level is entered), independent of the
# ancestry set's own A-embeds-B-embeds-A cycle guard (_execute_workflow_node
# below) -- a long *acyclic* chain of distinct workflows nesting into each
# other is still unbounded without this, and each level multiplies the
# loop guard's own already-generous budget (MAX_TOTAL_STEPS_PER_RUN) by
# however deep the chain goes.
MAX_WORKFLOW_NESTING_DEPTH = 10


@dataclass
class _RunContext:
    """Everything about a single run_workflow (or resume_workflow) call
    that every node executor down the call chain needs to see and mutate
    together -- bundled into one object instead of three loose parameters
    once #85 added a third (`ancestry`) to the two #81 already needed.
    Shared across every branch of one run, including ones on sibling
    sessions (see module docstring) -- mutating `visit_counts`/
    `total_steps` without a lock is safe because asyncio only ever runs
    one coroutine's synchronous code at a time; each check-then-increment
    completes before the next `await`. `ancestry` is read-only once
    built, so sharing it needs no such care."""

    visit_counts: dict[str, int]
    total_steps: list[int]
    # Workflow ids currently executing up this call chain, including the
    # current one -- #85's cycle guard for nested 'workflow' node types.
    ancestry: frozenset[str]
    # #96: None on every untraced run (the default -- see tracing.py's
    # module docstring). When set, `trace_ctx.parent_span_id` is this
    # run's own workflow_run span -- every node execution in
    # _run_node_with_retries nests its workflow_node_run span there, flat
    # under the run rather than chained node-to-node, which is simple and
    # still reads as "everything this run did" in the trace UI.
    # `run_span_id` is that same span's id, kept alongside trace_ctx so
    # _finalize_run (which doesn't otherwise see `ctx`) can close it out.
    trace_ctx: TraceContext | None = None
    run_span_id: str | None = None
    # #100: WorkflowRun.unattended, carried alongside the rest of this
    # run's shared state so execute_agent_node can read it without every
    # node executor in the call chain growing its own parameter for it
    # (the same reasoning ancestry/trace_ctx are here rather than loose
    # args). See that column's own docstring for what "unattended" means
    # and how it's derived/inherited.
    unattended: bool = False
    # #359: (merge_node_id, input) pairs a 'human_input' pause stranded --
    # sibling branches of a fan-out that reached a merge node before
    # another sibling paused. Banked by _resolve_merge_arrivals when a
    # pause wins its batch, persisted by _finalize_run's paused path onto
    # WorkflowRun.pending_merge_arrivals_json, and folded back into merge
    # resolution by resume_workflow once every waiting branch is answered.
    # Same shared-mutation safety story as visit_counts above.
    pending_merge_arrivals: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


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
    # The most recent node output this branch actually produced (#85) --
    # carried through every clean-success return so run_workflow's final
    # WorkflowRun.final_output is populated, which is what makes a nested
    # 'workflow' node's own output well-defined. None for a branch that
    # failed, paused, or never executed a node (e.g. no entry point).
    output: str | None = None


def _check_loop_guard(node: WorkflowNode, ctx: _RunContext) -> str | None:
    """Shared by every node executor (_run_branch, _run_merge_node,
    _pause_for_human_input): returns a detail message once `node` (or the
    run overall) has been visited too many times, else None. See module
    docstring's "Loops" section."""
    ctx.visit_counts[node.id] = ctx.visit_counts.get(node.id, 0) + 1
    ctx.total_steps[0] += 1
    if (
        ctx.visit_counts[node.id] > MAX_NODE_VISITS_PER_RUN
        or ctx.total_steps[0] > MAX_TOTAL_STEPS_PER_RUN
    ):
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


async def _begin_node(
    db: AsyncSession, run_id: str, workflow: Workflow, rivulet_id: str, node: WorkflowNode
) -> Rivulet:
    """Shared prologue for every node executor: records this node as the
    run's current position and announces it in the rivulet before doing
    any actual work."""
    rivulet = await db.get(Rivulet, rivulet_id)
    assert rivulet is not None
    await db.execute(
        update(WorkflowRun).where(WorkflowRun.id == run_id).values(current_node_id=node.id)
    )
    await db.commit()
    await _post_message(db, _step_message(rivulet, workflow, node))
    return rivulet


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


def _serialize_graph(nodes: dict[str, WorkflowNode], connections: list[WorkflowConnection]) -> str:
    """#84: captures exactly the fields the engine ever reads off a node
    or connection -- not the full row (created_at/vector_clock/etc. are
    irrelevant to execution) -- so a WorkflowRun can freeze the graph it
    started with. See _deserialize_graph and WorkflowRun.graph_snapshot_json."""
    return json.dumps(
        {
            "nodes": [
                {
                    "id": node.id,
                    "name": node.name,
                    "node_type": node.node_type,
                    "agent_id": node.agent_id,
                    "child_workflow_id": node.child_workflow_id,
                    "config_json": node.config_json,
                    "retry_max_attempts": node.retry_max_attempts,
                    "retry_backoff_seconds": node.retry_backoff_seconds,
                }
                for node in nodes.values()
            ],
            "connections": [
                {
                    "id": connection.id,
                    "from_node_id": connection.from_node_id,
                    "to_node_id": connection.to_node_id,
                    "condition_json": connection.condition_json,
                }
                for connection in connections
            ],
        }
    )


def _deserialize_graph(
    snapshot_json: str,
) -> tuple[dict[str, WorkflowNode], list[WorkflowConnection]]:
    """The other half of _serialize_graph: reconstructs detached
    WorkflowNode/WorkflowConnection instances (never added to a session,
    just plain attribute containers) from a WorkflowRun's frozen snapshot.
    The rest of the engine only ever reads attributes off these objects,
    never mutates or persists them directly, so a detached instance built
    straight from JSON is indistinguishable to it from one loaded live."""
    raw = json.loads(snapshot_json)
    nodes = {
        n["id"]: WorkflowNode(
            id=n["id"],
            name=n["name"],
            node_type=n["node_type"],
            agent_id=n["agent_id"],
            child_workflow_id=n.get("child_workflow_id"),
            config_json=n["config_json"],
            retry_max_attempts=n["retry_max_attempts"],
            retry_backoff_seconds=n["retry_backoff_seconds"],
        )
        for n in raw["nodes"]
    }
    connections = [
        WorkflowConnection(
            id=c["id"],
            from_node_id=c["from_node_id"],
            to_node_id=c["to_node_id"],
            condition_json=c["condition_json"],
        )
        for c in raw["connections"]
    ]
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
    db: AsyncSession,
    node: WorkflowNode,
    session_id: str,
    input_content: str,
    rivulet_id: str,
    run_id: str,
    ctx: _RunContext,
    node_trace_ctx: TraceContext | None = None,
) -> str:
    if node.node_type == "agent":
        return await execute_agent_node(
            db,
            node,
            session_id,
            input_content,
            node_trace_ctx,
            unattended=ctx.unattended,
            # #360: lets the node act on any builtin side-effect tools the
            # agent called (rivulet_id for the handlers' messages, ancestry
            # so an agent-triggered run_workflow is a guarded nested run).
            rivulet_id=rivulet_id,
            ancestry=ctx.ancestry,
        )
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
    if node.node_type == "workflow":
        return await _execute_workflow_node(
            db, node, rivulet_id, run_id, input_content, ctx, node_trace_ctx
        )
    raise ValueError(f"Unknown node_type {node.node_type!r}")


async def _execute_workflow_node(
    db: AsyncSession,
    node: WorkflowNode,
    rivulet_id: str,
    run_id: str,
    input_content: str,
    ctx: _RunContext,
    node_trace_ctx: TraceContext | None = None,
) -> str:
    """#85: invokes `node.child_workflow_id` as a nested run and returns
    its final_output as this node's own output. See module docstring's
    "Nested workflows" section for the ancestry-based cycle guard and why
    a nested pause becomes a failure here rather than propagating.

    #249: two guards added after the ancestry cycle check, in this order --
    a structural nesting-depth cap (`MAX_WORKFLOW_NESTING_DEPTH`, checked
    first since it needs no extra lookup and existing cycle-guard tests
    rely on the cycle error winning over a would-also-be-unpublished
    child), then a `published` re-check on `child_workflow` -- the same
    gate `workflows/trigger.py`'s `find_workflow_by_name` applies to the
    slash-command/agent-tool trigger paths, and `api/webhooks.py`/
    `workflows/scheduler.py` apply to theirs. Before this, a 'workflow'
    node could nest-run a child regardless of its publish state, which is
    exactly the kind of "still being built in the canvas" workflow
    `published` exists to keep un-triggerable -- and, since
    `WORKFLOW_NODE_SPEC`/`WORKFLOW_CONNECTION_SPEC` sync a workflow's
    graph independently of `WORKFLOW_SPEC.published` (now synced too, for
    this reason), a peer could otherwise have received a full, runnable
    graph for a workflow its own copy still correctly considers a draft.

    #96: `node_trace_ctx` (this node's own workflow_node_run span, from
    _run_node_with_retries) becomes the child run's *parent* span, not
    `ctx.trace_ctx` (this run's own root span) -- so a nested run's spans
    show up a level deeper than its sibling nodes, under the specific
    'workflow' node that invoked it."""
    if node.child_workflow_id is None:
        raise ValueError(f"Node {node.name!r} has no workflow selected")
    if node.child_workflow_id in ctx.ancestry:
        raise ValueError(
            f"Node {node.name!r} would create a cycle — that workflow is already running "
            "further up this chain"
        )
    if len(ctx.ancestry) >= MAX_WORKFLOW_NESTING_DEPTH:
        raise ValueError(
            f"Node {node.name!r} would nest workflows more than "
            f"{MAX_WORKFLOW_NESTING_DEPTH} levels deep"
        )
    child_workflow = await db.get(Workflow, node.child_workflow_id)
    if child_workflow is None:
        raise ValueError(f"Node {node.name!r} references a deleted workflow")
    if not child_workflow.published:
        raise ValueError(
            f"Node {node.name!r} references workflow /{child_workflow.name}, which isn't published"
        )
    rivulet = await db.get(Rivulet, rivulet_id)
    assert rivulet is not None

    child_run = await run_workflow(
        db,
        child_workflow,
        rivulet,
        input_content,
        triggered_by="workflow",
        triggered_by_id=run_id,
        ancestry=ctx.ancestry,
        trace_ctx=node_trace_ctx,
        # #100: a nested run inherits this run's unattended-ness rather
        # than being derived from the literal 'workflow' trigger string --
        # a workflow node inside a scheduled run is still unattended, and
        # one inside a live human-triggered run still isn't.
        unattended=ctx.unattended,
    )
    if child_run.status == "awaiting_human":
        detail = (
            f"Nested workflow /{child_workflow.name} paused for human input, which isn't "
            "supported inside a nested workflow yet"
        )
        # Leaving the child at 'awaiting_human' would dangle it: the
        # parent node is about to fail (and this run along with it), but
        # the child -- sharing this same rivulet -- would still look like
        # a live, resumable pause to find_awaiting_workflow_run the next
        # time a human types here, disconnected from the parent that gave
        # up on it. Fail it too and clear the pause instead.
        child_run.status = "failed"
        child_run.error_message = detail
        child_run.completed_at = utcnow_iso()
        rivulet.status = "active"
        await db.commit()
        raise RuntimeError(detail)
    if child_run.status == "failed":
        raise RuntimeError(
            f"Nested workflow /{child_workflow.name} failed: {child_run.error_message}"
        )
    return child_run.final_output or ""


_UNATTENDED_TRIGGERS = frozenset({"schedule", "remediation", "webhook", "eval"})


async def run_workflow(
    db: AsyncSession,
    workflow: Workflow,
    rivulet: Rivulet,
    input_content: str,
    *,
    triggered_by: str,
    triggered_by_id: str | None,
    ancestry: frozenset[str] = frozenset(),
    trace_ctx: TraceContext | None = None,
    unattended: bool | None = None,
    run_id: str | None = None,
) -> WorkflowRun:
    """Run `workflow` against `rivulet`, starting with `input_content` as
    the entry node's input. `triggered_by`/`triggered_by_id` record who
    kicked this off ('human' from api/rivulets.py's slash-command
    interceptor, 'agent' from tools/builtin/run_workflow.py, or
    'workflow' for a nested invocation, #85 -- see WorkflowRun's
    docstring) for the same audit purpose AgentRun.source does for
    dispatcher-originated LLM calls. `ancestry` is only ever passed by a
    nested invocation (workflows/engine.py's own `_execute_workflow_node`)
    -- an ordinary top-level call leaves it empty, so this run's own id is
    the whole ancestry chain. Returns the WorkflowRun row (already
    committed) regardless of outcome — callers inspect `.status` rather
    than this raising, mirroring dispatch_and_respond's "failures are
    recorded, not propagated" style.

    `trace_ctx` (#96) is None on every untraced call site (the default) --
    see tracing.py's module docstring. When set, this run gets its own
    workflow_run span nested under `trace_ctx.parent_span_id`, and every
    node this run executes nests under *that* span (see _RunContext's
    docstring).

    `unattended` (#100, WorkflowRun.unattended's docstring): every
    top-level call site (scheduler.py, dispatch/service.py, api/
    rivulets.py, evals/runner.py, this module's own remediation trigger)
    leaves this at its default of None, which derives it from
    `triggered_by` (_UNATTENDED_TRIGGERS) -- only `_execute_workflow_node`
    passes it explicitly, inheriting the parent run's value rather than
    letting a nested run's literal 'workflow' trigger derive to False.

    `run_id` (#242): every call site leaves this at its default of None,
    letting WorkflowRun.id fall back to its normal uuid7 default. The one
    exception is api/webhooks.py, which needs this run's id *before*
    execution starts -- it hands the run off to a BackgroundTask rather
    than awaiting it inline, so the id it commits to in its 202 response
    has to be chosen up front rather than read back off the row this
    function would otherwise generate.
    """
    if unattended is None:
        unattended = triggered_by in _UNATTENDED_TRIGGERS
    nodes, connections = await _load_nodes_and_connections(db, workflow.id)

    run = WorkflowRun(
        workflow_id=workflow.id,
        rivulet_id=rivulet.id,
        triggered_by=triggered_by,
        triggered_by_id=triggered_by_id,
        unattended=unattended,
        input_content=input_content,
        # #84: freezes the graph this run executes against -- a later
        # resume_workflow reads it back rather than re-querying the live
        # (possibly since-edited) WorkflowNode/WorkflowConnection rows.
        graph_snapshot_json=_serialize_graph(nodes, connections),
    )
    if run_id is not None:
        run.id = run_id
    db.add(run)
    await db.flush()
    run_span_id = await start_span(
        db, trace_ctx, span_type="workflow_run", entity_id=run.id, name=f"/{workflow.name}"
    )
    run_trace_ctx = TraceContext(trace_ctx.trace_id, run_span_id) if trace_ctx is not None else None

    if rivulet.agentos_session_id is None:
        rivulet.agentos_session_id = rivulet.id  # FR-12.2: one AgentOS session per rivulet

    entry_node_id = _entry_node_id(connections)
    if entry_node_id is None or entry_node_id not in nodes:
        run.status = "failed"
        run.error_message = "Workflow has no entry point"
        run.completed_at = utcnow_iso()
        await finish_span(db, run_span_id, status="error")
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

    ctx = _RunContext(
        visit_counts={},
        total_steps=[0],
        ancestry=ancestry | {workflow.id},
        trace_ctx=run_trace_ctx,
        run_span_id=run_span_id,
        unattended=unattended,
    )
    entry_outcome = await _advance(
        db, run.id, workflow, rivulet.id, nodes, connections, entry_node_id, input_content, ctx
    )
    outcome = await _resolve_merge_arrivals(
        [entry_outcome], db, run.id, workflow, rivulet.id, nodes, connections, ctx
    )

    return await _finalize_run(
        db, workflow, rivulet, run, outcome, run_span_id, run_trace_ctx, ctx=ctx
    )


async def _finalize_run(
    db: AsyncSession,
    workflow: Workflow,
    rivulet: Rivulet,
    run: WorkflowRun,
    outcome: _BranchOutcome,
    run_span_id: str | None = None,
    trace_ctx: TraceContext | None = None,
    ctx: _RunContext | None = None,
) -> WorkflowRun:
    """Shared by run_workflow and resume_workflow. A paused outcome's
    status/current_node_id were already committed inside
    _pause_for_human_input, possibly on a different (sibling) session --
    refresh rather than reassign so the returned `run` reflects that write
    instead of clobbering it with a stale in-memory value. The paused path
    also persists `ctx`'s banked merge arrivals and its final loop-guard
    counters (#359): _pause_for_human_input snapshotted the counters at
    the moment *its* branch stopped, but sibling branches kept executing
    (and arriving at merges) until the whole gather settled, and only here
    is that settled state known.

    A finalized (non-paused) run has no branch left to wait on (#359): a
    failed sibling used to finalize the run while another branch sat
    paused, leaving its WorkflowNodeRun 'awaiting_human' forever and
    Rivulet.status='paused' keeping the banner up with no run left for
    find_awaiting_workflow_run to match -- the next typed message went to
    dispatch under a stuck banner. Any dangling waits are failed here and
    the rivulet's pause released.

    triggered_by='eval' (#95) is excluded from the failed-run notification
    path entirely: an eval suite deliberately exercising a failure case
    must not fire a production remediation workflow or @mention a real
    on-call agent. Unlike 'remediation' -- which only self-guards inside
    _maybe_trigger_remediation (a remediation run failing should still
    reach _maybe_notify_on_call_agent, so a human finds out) -- an eval
    run has no legitimate reason to trigger either, so it's excluded at
    this single outer gate instead of threading a second guard into both
    helpers.

    #96: `run_span_id`/`trace_ctx` describe *this* run's own span -- a
    paused outcome leaves it open (unfinished) rather than closing it, the
    same way `run.status` itself is left at 'running'/'awaiting_human'
    rather than finalized here; resume_workflow re-attaches to it later
    (see that function's own docstring). Remediation/on-call notification
    spans nest under this run's own span (`trace_ctx`, whose
    parent_span_id is `run_span_id`), reading as "this run's failure
    caused this" in the trace tree."""
    if outcome.paused:
        if ctx is not None:
            await db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run.id)
                .values(
                    pending_merge_arrivals_json=json.dumps(ctx.pending_merge_arrivals),
                    visit_counts_json=json.dumps(ctx.visit_counts),
                    total_steps=ctx.total_steps[0],
                )
            )
            await db.commit()
        await db.refresh(run)
        return run

    run.status = "failed" if outcome.failed else "completed"
    run.error_message = outcome.error_message
    run.final_output = outcome.output if not outcome.failed else None
    run.completed_at = utcnow_iso()
    run.pending_merge_arrivals_json = "[]"
    dangling_result = await db.execute(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.status == "awaiting_human",
        )
    )
    dangling = list(dangling_result.scalars().all())
    for node_run in dangling:
        node_run.status = "failed"
        node_run.error_message = "the run ended while this step was still waiting for a reply"
        node_run.completed_at = utcnow_iso()
        dangling_span_id = await db.scalar(
            select(RunSpan.id).where(
                RunSpan.span_type == "workflow_node_run", RunSpan.entity_id == node_run.id
            )
        )
        await finish_span(db, dangling_span_id, status="error")
    if dangling:
        await db.refresh(rivulet)
        if rivulet.status == "paused":
            rivulet.status = "active"
    await finish_span(db, run_span_id, status="error" if outcome.failed else "completed")
    await db.commit()

    if run.status == "failed" and run.triggered_by != "eval":
        await _maybe_trigger_remediation(db, workflow, rivulet, run, trace_ctx)
        await _maybe_notify_on_call_agent(db, workflow, rivulet, run, trace_ctx)

    return run


async def _maybe_trigger_remediation(
    db: AsyncSession,
    workflow: Workflow,
    rivulet: Rivulet,
    failed_run: WorkflowRun,
    trace_ctx: TraceContext | None = None,
) -> None:
    """#94 layer 2: `Workflow.on_failure_workflow_id`, when set, fires
    automatically whenever a run of `workflow` finishes `failed` --
    reusing the same "one workflow invoking another" plumbing #85's
    node_type='workflow' step already established (`_execute_workflow_node`
    calls this same `run_workflow`), just triggered by a run's finalize
    instead of a graph node. Runs as a *fresh top-level* run_workflow call
    (default `ancestry`), not a nested node execution -- #85's
    ancestry-based cycle guard only ever sees a single call chain's stack,
    which doesn't exist here since this fires after the failing run has
    already fully finished and committed.

    That's why the depth-1 cap has to be a separate, simpler mechanism:
    `failed_run.triggered_by == 'remediation'` refuses to trigger further
    remediation, so a remediation workflow that references (directly, or
    via a longer cycle) the very workflow it's remediating can ping-pong
    at most once before stopping on its own -- no unbounded loop, and no
    need to reject a *self*-referencing on_failure_workflow_id at save
    time the way child_workflow_id rejects a self-referencing node_type=
    'workflow' step (see Workflow.on_failure_workflow_id's docstring):
    "on failure, retry this same workflow once" is a legitimate shape,
    and this cap already makes it safe.

    Deliberately not wired into run_workflow's own "no entry point"
    early-return failure -- that's a configuration error a remediation
    attempt can't possibly fix, exactly the kind of failure #94 itself
    calls out as not worth auto-remediating.

    #292: re-checks `remediation_workflow.published` here too, the same
    gate #249 added to `_execute_workflow_node`'s nested 'workflow' node
    -- this fires unattended (`triggered_by='remediation'` is in
    `_UNATTENDED_TRIGGERS`) off a run's finalize, with no human in the
    loop to notice an accidental draft reference the way a live slash
    command or agent tool call would. Silently skipping (like the
    `remediation_workflow is None` case just above) rather than raising:
    there's no in-flight run left to fail here, `failed_run` already
    finished and committed before this was ever called."""
    if workflow.on_failure_workflow_id is None or failed_run.triggered_by == "remediation":
        return
    remediation_workflow = await db.get(Workflow, workflow.on_failure_workflow_id)
    if remediation_workflow is None or not remediation_workflow.published:
        return

    await _post_message(
        db,
        Message(
            rivulet_id=rivulet.id,
            sender_type="system",
            sender_name="system",
            content=(
                f"Workflow /{workflow.name} failed — triggering remediation workflow "
                f"/{remediation_workflow.name}."
            ),
            content_type="system_alert",
        ),
    )
    remediation_input = (
        f"Workflow /{workflow.name} run failed.\n"
        f"Original input: {failed_run.input_content}\n"
        f"Error: {failed_run.error_message}"
    )
    await run_workflow(
        db,
        remediation_workflow,
        rivulet,
        remediation_input,
        triggered_by="remediation",
        triggered_by_id=failed_run.id,
        trace_ctx=trace_ctx,
    )


async def _resolve_on_call_agent_id(db: AsyncSession, workflow: Workflow) -> str | None:
    """`Workflow.on_call_agent_id` if set; otherwise the workspace-wide
    'workflows.default_on_call_agent_id' setting (api/settings.py),
    unvalidated JSON like every other row in that table -- a stale/typo'd
    id there just means `_maybe_notify_on_call_agent`'s own `db.get`
    finds nothing and skips notifying, not an error here."""
    if workflow.on_call_agent_id is not None:
        return workflow.on_call_agent_id
    setting = await db.get(WorkspaceSetting, "workflows.default_on_call_agent_id")
    if setting is None:
        return None
    value = json.loads(setting.value)
    return value if isinstance(value, str) else None


async def _maybe_notify_on_call_agent(
    db: AsyncSession,
    workflow: Workflow,
    rivulet: Rivulet,
    failed_run: WorkflowRun,
    trace_ctx: TraceContext | None = None,
) -> None:
    """#94 layer 3: an alternative or complement to `_maybe_trigger_
    remediation` -- for a failure where what's needed is an agent's
    judgment rather than a deterministic recovery routine, `@mention`s
    the resolved on-call agent (`_resolve_on_call_agent_id`) instead of
    (or, since the two are independently configurable, alongside)
    triggering a fixed remediation workflow.

    Reuses the ordinary human-message dispatch path
    (`dispatch.dispatch_and_respond`) rather than a separate notification
    mechanism: the alert is posted as a real Message whose content
    contains an `@mention`, then run through the exact same two-stage
    dispatcher (dispatch/engine.py's `match_mentions`) a human typing
    that text in this rivulet's channel would hit -- including its
    existing "only an agent on this channel's own team is ever matched"
    gate. An on-call agent configured for a channel its own team doesn't
    cover silently doesn't get dispatched, the same as an ordinary
    `@mention` of someone off the team would -- not special-cased here.
    Local import of `dispatch_and_respond`: `dispatch/service.py` already
    imports `run_workflow` from this module lazily inside its own
    run_workflow-tool handling for the identical circular-import reason
    (that module docstring's own note on `_handle_run_workflow_trigger`).

    Deliberately no attempt/frequency cap here unlike `_maybe_trigger_
    remediation`'s depth-1 guard: a chronically-failing *scheduled*
    workflow is already bounded by WorkflowSchedule's own consecutive-
    failure auto-disable (#92/#93), and a human/agent-triggered run
    failing repeatedly requires someone actively re-triggering it each
    time -- neither is the kind of self-sustaining loop that guard
    exists to stop."""
    agent_id = await _resolve_on_call_agent_id(db, workflow)
    if agent_id is None:
        return
    agent = await db.get(Agent, agent_id)
    if agent is None:
        return
    channel = await db.get(Channel, rivulet.channel_id)
    if channel is None:
        return

    alert_content = f"@{agent.name} workflow /{workflow.name} failed: {failed_run.error_message}"
    message = await _post_message(
        db,
        Message(
            rivulet_id=rivulet.id,
            sender_type="system",
            sender_name="system",
            content=alert_content,
            content_type="system_alert",
        ),
    )

    from rivulets.dispatch import dispatch_and_respond

    agent_messages = await dispatch_and_respond(
        db,
        rivulet,
        channel,
        alert_content,
        triggering_message_id=message.id,
        trace_ctx=trace_ctx,
    )
    await db.commit()
    for agent_message in agent_messages:
        await publish_current_state(db, "message", agent_message.id)


async def resume_workflow(db: AsyncSession, run: WorkflowRun, human_reply: str) -> WorkflowRun:
    """Continues a WorkflowRun paused by a 'human_input' node (#83, see
    _pause_for_human_input), called from api/rivulets.py's post_message
    once workflows/trigger.py's find_awaiting_workflow_run identifies the
    next message in that rivulet as the reply. `human_reply` becomes the
    paused node's output — the same "a node's output becomes the next
    node's input" contract every other node type honors (issue #24) — and
    the walk continues from there via _follow_edges. The loop guard
    resumes where it left off (#249): `_pause_for_human_input` persists
    the original `_RunContext`'s `visit_counts`/`total_steps` onto
    `run.visit_counts_json`/`run.total_steps` right before pausing, and
    this function seeds its new `_RunContext` from those columns instead
    of starting both at zero -- without that, a looping graph that pauses
    once per iteration (a 'human_input' node inside the loop) could reset
    its own guard on every pause and run past MAX_NODE_VISITS_PER_RUN /
    MAX_TOTAL_STEPS_PER_RUN without ever tripping it. Ancestry resets to
    just this workflow's own id -- a resumed run is always a top-level one
    (#85's module docstring section on why a nested run can never be the
    one paused).

    #84: hydrates the graph from `run.graph_snapshot_json` rather than
    re-querying the live WorkflowNode/WorkflowConnection rows -- a
    workflow edited in the builder while a run sits paused shouldn't
    change what that run does when it resumes.

    #96: doesn't accept or continue a trace_ctx -- tracing intentionally
    doesn't span a pause/resume boundary (RunTrace's docstring), so no new
    spans are recorded for anything this resumed run goes on to execute.
    It does look up and re-attach to the *original* run_span (if this run
    was traced when it started), purely so that span -- left 'running' by
    _finalize_run's paused early-return -- eventually gets closed with the
    run's real final status instead of showing 'running' forever.

    #359: the run's own 'awaiting_human' WorkflowNodeRun row, not
    `current_node_id`, decides which node the reply answers.
    `current_node_id` is position telemetry every `_begin_node` call
    updates -- inside a fan-out, a sibling branch still executing after
    `_pause_for_human_input` committed its pause overwrites it with
    whatever node that sibling moved onto, so resuming from it walked the
    graph from the wrong node (and skipped completing the wait) whenever
    the pause wasn't the run's last write. It's only the fallback for a
    run with no awaiting row left (its node deleted from the snapshot's
    perspective is handled below either way)."""
    workflow = await db.get(Workflow, run.workflow_id)
    rivulet = await db.get(Rivulet, run.rivulet_id)
    assert workflow is not None
    assert rivulet is not None

    run_span_id = await db.scalar(
        select(RunSpan.id).where(RunSpan.span_type == "workflow_run", RunSpan.entity_id == run.id)
    )

    nodes, connections = _deserialize_graph(run.graph_snapshot_json)
    paused_node_run = await db.scalar(
        select(WorkflowNodeRun)
        .where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.status == "awaiting_human",
        )
        .order_by(WorkflowNodeRun.started_at.desc())
        .limit(1)
    )
    paused_node_id = paused_node_run.node_id if paused_node_run is not None else run.current_node_id
    assert paused_node_id is not None  # set by _pause_for_human_input
    paused_node = nodes.get(paused_node_id)
    if paused_node is None:
        return await _finalize_run(
            db,
            workflow,
            rivulet,
            run,
            _BranchOutcome(
                failed=True, error_message="The paused step was deleted before you replied"
            ),
            run_span_id,
        )

    if paused_node_run is not None:
        paused_node_run.status = "completed"
        paused_node_run.output_content = human_reply
        # #96: closes out the span _pause_for_human_input opened and left
        # 'running' -- the only span-affecting thing resume_workflow does
        # (no new spans for whatever runs after this; see this function's
        # own docstring).
        paused_span_id = await db.scalar(
            select(RunSpan.id).where(
                RunSpan.span_type == "workflow_node_run", RunSpan.entity_id == paused_node_run.id
            )
        )
        await finish_span(db, paused_span_id, status="completed")
        paused_node_run.completed_at = utcnow_iso()

    rivulet.status = "active"
    run.status = "running"
    await db.commit()

    ctx = _RunContext(
        # #249: restores the loop guard's counters as of the pause that
        # led here (this function's own docstring), instead of starting a
        # fresh budget every resume.
        visit_counts=json.loads(run.visit_counts_json),
        total_steps=[run.total_steps],
        ancestry=frozenset({workflow.id}),
        # #100: restores what run_workflow derived/inherited at this run's
        # original start -- a fresh _RunContext here has no other way to
        # know it, since a pause/resume boundary doesn't carry the old one
        # forward (this function's own docstring).
        unattended=run.unattended,
        # #359: restores the merge arrivals sibling branches banked before
        # the pause (WorkflowRun's own docstring), so the merge they
        # reached can eventually fire with their inputs too.
        pending_merge_arrivals=[
            (str(merge_id), str(merge_input))
            for merge_id, merge_input in json.loads(run.pending_merge_arrivals_json)
        ],
    )
    outcome = await _follow_edges(
        db, run.id, workflow, rivulet.id, nodes, connections, paused_node.id, human_reply, ctx
    )

    if not outcome.failed and not outcome.paused:
        # #359: one reply resolves one branch. If another branch of this
        # run is still sitting on its own 'human_input' node (parallel
        # 'human_input' fan-out -- current_node_id used to be last-writer-
        # wins and the losing branch waited forever), re-pause on it
        # instead of finalizing, banking this branch's merge arrival (if
        # any) for the final join.
        next_waiting = await db.scalar(
            select(WorkflowNodeRun)
            .where(
                WorkflowNodeRun.workflow_run_id == run.id,
                WorkflowNodeRun.status == "awaiting_human",
            )
            .order_by(WorkflowNodeRun.started_at)
            .limit(1)
        )
        if next_waiting is not None:
            if outcome.arrived_at_merge is not None:
                ctx.pending_merge_arrivals.append(outcome.arrived_at_merge)
            return await _repause_on_waiting_branch(
                db, run, workflow, rivulet, nodes, next_waiting, ctx
            )
        # #359: every wait is answered -- fold the banked sibling arrivals
        # (and this branch's own, if it stopped at a merge) into one
        # resolution batch so a shared merge fires with everything that
        # actually reached it, not just the resumed branch's input.
        banked = [
            _BranchOutcome(failed=False, arrived_at_merge=(merge_id, merge_input))
            for merge_id, merge_input in ctx.pending_merge_arrivals
        ]
        ctx.pending_merge_arrivals = []
        outcome = await _resolve_merge_arrivals(
            [outcome, *banked], db, run.id, workflow, rivulet.id, nodes, connections, ctx
        )

    return await _finalize_run(db, workflow, rivulet, run, outcome, run_span_id, ctx=ctx)


async def _repause_on_waiting_branch(
    db: AsyncSession,
    run: WorkflowRun,
    workflow: Workflow,
    rivulet: Rivulet,
    nodes: dict[str, WorkflowNode],
    waiting_node_run: WorkflowNodeRun,
    ctx: _RunContext,
) -> WorkflowRun:
    """#359: parallel 'human_input' branches resolve one reply at a time.
    resume_workflow just completed one branch's wait, but
    `waiting_node_run` (another branch's, created by _pause_for_human_input
    during the original fan-out) is still open -- point the run back at it
    and re-pause, re-announcing the step so the human knows which question
    is now current, instead of finalizing with the other branch abandoned.
    Persists the same pause-boundary state _finalize_run's paused path
    does (banked merge arrivals, loop-guard counters), since finalize is
    exactly what this path skips."""
    await db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id)
        .values(
            current_node_id=waiting_node_run.node_id,
            status="awaiting_human",
            visit_counts_json=json.dumps(ctx.visit_counts),
            total_steps=ctx.total_steps[0],
            pending_merge_arrivals_json=json.dumps(ctx.pending_merge_arrivals),
        )
    )
    rivulet.status = "paused"
    await db.commit()
    await db.refresh(run)
    node = nodes.get(waiting_node_run.node_id)
    if node is not None:
        await _post_message(
            db,
            Message(
                rivulet_id=rivulet.id,
                sender_type="system",
                sender_name="system",
                content=(
                    f"⏸ Workflow /{workflow.name} is waiting for your reply at step {node.name!r}."
                ),
                content_type="system_alert",
            ),
        )
        publish(
            rivulet.id,
            "system_alert",
            {"type": "workflow_awaiting_human", "workflow_id": workflow.id, "node_id": node.id},
        )
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
    ctx: _RunContext,
) -> _BranchOutcome:
    """Executes `node_id` and everything reachable from it on this branch,
    recursing (or fanning out — see module docstring) for each matching
    outbound edge."""
    node = nodes.get(node_id)
    if node is None:
        return _BranchOutcome(failed=False)  # edge to a since-deleted node: a quiet dead end

    detail = _check_loop_guard(node, ctx)
    if detail is not None:
        await _post_alert(db, rivulet_id, workflow, node, detail)
        return _BranchOutcome(failed=True, error_message=detail, node_name=node.name)

    rivulet = await _begin_node(db, run_id, workflow, rivulet_id, node)
    session_id = rivulet.agentos_session_id
    assert session_id is not None  # run_workflow sets this before any branch starts

    output, failure = await _run_node_with_retries(
        db, run_id, node, session_id, input_content, rivulet_id, ctx
    )

    if isinstance(failure, ConditionNotMetError):
        # the node's own gate said stop here, not a failure -- input_content
        # is the most recent real output this branch produced (the gate
        # itself never advanced it).
        return _BranchOutcome(failed=False, output=input_content)

    if failure is not None:
        await _post_alert(db, rivulet_id, workflow, node, str(failure))
        return _BranchOutcome(failed=True, error_message=str(failure), node_name=node.name)

    assert output is not None
    await _post_message(db, _output_message(rivulet, node, output))

    return await _follow_edges(
        db, run_id, workflow, rivulet_id, nodes, connections, node.id, output, ctx
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
    ctx: _RunContext,
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
        return _BranchOutcome(failed=False, output=output)  # terminal node, or no edge matched

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
            ctx,
        )

    async def _in_new_session(to_node_id: str) -> _BranchOutcome:
        async with session_scope() as branch_db:
            return await _advance(
                branch_db, run_id, workflow, rivulet_id, nodes, connections, to_node_id, output, ctx
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
            ctx,
        ),
        *(_in_new_session(edge.to_node_id) for edge in matching[1:]),
    )
    return await _resolve_merge_arrivals(
        list(outcomes), db, run_id, workflow, rivulet_id, nodes, connections, ctx
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
    ctx: _RunContext,
) -> _BranchOutcome:
    """The one place a branch is about to move onto a node: if that node
    is a 'merge', stop here and report the arrival instead of executing it
    (a merge node only ever runs via _run_merge_node, once per group of
    siblings -- see _resolve_merge_arrivals). If it's a 'human_input'
    node, pause instead (#83, _pause_for_human_input). Otherwise just
    _run_branch as normal (this includes 'workflow' nodes, #85 -- nested
    invocation is a normal, synchronous node executor, not a special
    interception point the way merge/human_input are)."""
    target = nodes.get(node_id)
    if target is not None and target.node_type == "merge":
        return _BranchOutcome(failed=False, arrived_at_merge=(node_id, input_content))
    if target is not None and target.node_type == "human_input":
        return await _pause_for_human_input(
            db, run_id, workflow, rivulet_id, target, input_content, ctx
        )
    return await _run_branch(
        db, run_id, workflow, rivulet_id, nodes, connections, node_id, input_content, ctx
    )


async def _pause_for_human_input(
    db: AsyncSession,
    run_id: str,
    workflow: Workflow,
    rivulet_id: str,
    node: WorkflowNode,
    input_content: str,
    ctx: _RunContext,
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
    detail = _check_loop_guard(node, ctx)
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
    await db.flush()
    # #96: left 'running' -- resume_workflow finishes it once a reply
    # arrives, mirroring WorkflowNodeRun's own updated-in-place (not a new
    # row) treatment of a pause.
    await start_span(
        db, ctx.trace_ctx, span_type="workflow_node_run", entity_id=node_run.id, name=node.name
    )
    await db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .values(
            current_node_id=node.id,
            status="awaiting_human",
            # #249: snapshot the loop guard's in-flight counters so a
            # resume doesn't restart them at zero -- see WorkflowRun's own
            # docstring.
            visit_counts_json=json.dumps(ctx.visit_counts),
            total_steps=ctx.total_steps[0],
        )
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
    ctx: _RunContext,
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
    needed *another* sibling's contribution can't fire yet either way.
    The batch's arrivals are banked on ctx.pending_merge_arrivals (#359)
    for resume_workflow to fold back into resolution once every wait is
    answered, rather than dropped -- see module docstring's pause/resume
    section.

    When nothing arrived at a merge and nothing paused, this batch is
    just a set of independently-terminated branches -- the first one
    (in the fanned-out edges' own order, not completion order) supplies
    the aggregate output (#85), matching the "first branch is primary"
    convention _follow_edges already uses for which session stays shared."""
    failed = next((o for o in outcomes if o.failed), None)
    if failed is not None:
        return failed

    paused = next((o for o in outcomes if o.paused), None)
    if paused is not None:
        # #359: siblings that already reached a merge node can't fire it
        # yet (the paused branch may still be headed there too) -- bank
        # their arrivals for resume_workflow to fold back into resolution
        # once every wait is answered, instead of dropping them with this
        # batch.
        for outcome in outcomes:
            if outcome.arrived_at_merge is not None:
                ctx.pending_merge_arrivals.append(outcome.arrived_at_merge)
        return paused

    merge_groups: dict[str, list[str]] = {}
    for outcome in outcomes:
        if outcome.arrived_at_merge is not None:
            merge_id, merge_input = outcome.arrived_at_merge
            merge_groups.setdefault(merge_id, []).append(merge_input)

    if not merge_groups:
        output = next((o.output for o in outcomes if o.output is not None), None)
        return _BranchOutcome(failed=False, output=output)

    groups = list(merge_groups.items())

    async def _in_new_session(merge_id: str, inputs: list[str]) -> _BranchOutcome:
        async with session_scope() as branch_db:
            return await _run_merge_node(
                branch_db, run_id, workflow, rivulet_id, nodes, connections, merge_id, inputs, ctx
            )

    merge_outcomes = await asyncio.gather(
        _run_merge_node(
            db, run_id, workflow, rivulet_id, nodes, connections, groups[0][0], groups[0][1], ctx
        ),
        *(_in_new_session(merge_id, inputs) for merge_id, inputs in groups[1:]),
    )
    return await _resolve_merge_arrivals(
        list(merge_outcomes), db, run_id, workflow, rivulet_id, nodes, connections, ctx
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
    ctx: _RunContext,
) -> _BranchOutcome:
    """Executes a merge node exactly once for the group of sibling inputs
    _resolve_merge_arrivals collected, then continues the walk from there
    -- the same visit/step accounting, message posting, and edge-following
    _run_branch does for every other node type, just with a pre-combined
    list of inputs instead of a single predecessor's output."""
    node = nodes.get(merge_node_id)
    if node is None:
        return _BranchOutcome(failed=False)

    detail = _check_loop_guard(node, ctx)
    if detail is not None:
        await _post_alert(db, rivulet_id, workflow, node, detail)
        return _BranchOutcome(failed=True, error_message=detail, node_name=node.name)

    rivulet = await _begin_node(db, run_id, workflow, rivulet_id, node)
    session_id = rivulet.agentos_session_id
    assert session_id is not None

    output, failure = await _run_node_with_retries(
        db, run_id, node, session_id, json.dumps(inputs), rivulet_id, ctx
    )

    if failure is not None:
        await _post_alert(db, rivulet_id, workflow, node, str(failure))
        return _BranchOutcome(failed=True, error_message=str(failure), node_name=node.name)

    assert output is not None
    await _post_message(db, _output_message(rivulet, node, output))

    return await _follow_edges(
        db, run_id, workflow, rivulet_id, nodes, connections, node.id, output, ctx
    )


async def _run_node_with_retries(
    db: AsyncSession,
    run_id: str,
    node: WorkflowNode,
    session_id: str,
    input_content: str,
    rivulet_id: str,
    ctx: _RunContext,
) -> tuple[str | None, Exception | None]:
    """Executes `node`, retrying up to `node.retry_max_attempts` additional
    times on failure with `node.retry_backoff_seconds` between attempts —
    one WorkflowNodeRun row per attempt (db/models.py's docstring on why:
    keeps retry history inspectable). A ConditionNotMetError is never
    retried — it isn't a transient failure, it's the node behaving exactly
    as configured.

    #96: one workflow_node_run span per attempt too, nested under
    `ctx.run_span_id` (all of a run's nodes sit flat under its own root
    span rather than chained node-to-node — see _RunContext's docstring).
    An 'agent' or 'workflow' node type nests further under *this specific
    attempt's* span (see _execute_node/_execute_workflow_node)."""
    max_attempts = max(1, node.retry_max_attempts + 1)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        node_run = WorkflowNodeRun(
            workflow_run_id=run_id, node_id=node.id, attempt=attempt, input_content=input_content
        )
        db.add(node_run)
        await db.flush()
        node_span_id = await start_span(
            db, ctx.trace_ctx, span_type="workflow_node_run", entity_id=node_run.id, name=node.name
        )
        node_trace_ctx = (
            TraceContext(ctx.trace_ctx.trace_id, node_span_id)
            if ctx.trace_ctx is not None
            else None
        )
        try:
            output = await _execute_node(
                db, node, session_id, input_content, rivulet_id, run_id, ctx, node_trace_ctx
            )
        except ConditionNotMetError as exc:
            node_run.status = "skipped"
            node_run.error_message = str(exc)
            node_run.completed_at = utcnow_iso()
            await finish_span(db, node_span_id, status="completed")
            await db.commit()
            return None, exc
        except Exception as exc:  # noqa: BLE001 — any node failure is retried/reported uniformly
            last_error = exc
            node_run.status = "failed"
            node_run.error_message = str(exc)
            node_run.completed_at = utcnow_iso()
            await finish_span(db, node_span_id, status="error")
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
            await finish_span(db, node_span_id, status="completed")
            await db.commit()
            return output, None

    return None, last_error
