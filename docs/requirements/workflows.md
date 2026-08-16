# FR-13: Workflows

Status: **shipped** — original backend slice (FR-13.1–13.6) plus the
follow-on work listed under "Shipped after the original slice." This is
no longer a backend-only, linear-only, no-canvas slice.

This is the first FR doc in the repo — FR-1 through FR-12 exist only as
inline references in code comments/docstrings (dispatcher, handoff, sync,
etc.), not as standalone specs. FR-13 starts the convention of writing
these down; back-filling FR-1–12 is a separate, unscoped cleanup, not
part of this work.

## FR-13.1 — Workflow definition

A `Workflow` (`db/models.py`) is a named, saved automation: an ordered
graph of `WorkflowNode`s connected by `WorkflowConnection` edges.
`Workflow.name` doubles as its slash-command trigger (FR-13.4) — one
field, not two, since the two never diverge in this design.

A node is one of:

- **`agent`** — invokes an existing `Agent` via the same
  `agentos.run_agent` entry point the dispatcher uses
  (`workflows/nodes.py:execute_agent_node`).
- **`transform`** — a deterministic string template (`{input}`
  substitution), no LLM call.
- **`summarize`** — an ad-hoc LLM call (no dedicated `Agent` row
  needed), reusing the dispatcher's "pick a cheap model" policy
  (`dispatch/rule_generation.py:pick_dispatcher_model`).
- **`conditional`** — a substring predicate against the input; on
  failure, ends **that branch** early (not a run failure — the workflow
  was designed to stop there). Distinct from edge `condition_json`
  (FR-13.2): this is a solo gate, not a labeled if/else.
- **`merge`** — joins sibling fan-out branches into one execution
  (`workflows/nodes.py:execute_merge_node`). Resolution happens at the
  nearest ancestor fan-out (or the top-level run), so siblings a
  different number of hops from the merge still join.
- **`human_input`** — pauses the run until the next human message in
  that rivulet (FR-13.8).
- **`workflow`** — invokes another workflow as a nested child
  (`WorkflowNode.child_workflow_id`).

Each node supports a configurable retry policy (`retry_max_attempts`,
`retry_backoff_seconds`) for transient failures. Canvas coordinates
(`position_x` / `position_y`) are stored on the node for the visual
builder.

## FR-13.2 — Node graph

`WorkflowConnection` is the source of truth for execution order: an
edge with `from_node_id IS NULL` marks the entry point. A workflow
still starts at exactly one node (the API enforces a single entry
connection).

The engine (`workflows/engine.py`) is no longer linear-only:

- A node may have several outbound edges. Each edge's `condition_json`
  decides whether it's followed (`{"contains": "text"}` /
  `{"not_contains": "text"}`, or absent/null = always). Matching
  edges fan out concurrently.
- Loops fall out of the same mechanism (an edge may point at an
  already-visited node). Two unconfigurable caps stop runaway cost:
  `MAX_NODE_VISITS_PER_RUN` per node and `MAX_TOTAL_STEPS_PER_RUN`
  across the run.
- Real if/else is complementary `contains` / `not_contains` conditions
  on two outbound edges, not the `conditional` node's own
  stop-the-branch config.

## FR-13.3 — Execution & telemetry

Running a workflow produces a `WorkflowRun` (one row, `status`:
running/completed/failed/awaiting_human) and one `WorkflowNodeRun` per
node execution attempt (one row per retry attempt, not one row updated
in place, so retry history stays inspectable). Both are **local
execution state, not synced** — same treatment as
`AgentRun`/`DispatchDecision`.

Each node's execution posts a visible step-indicator message into the
rivulet (`content_type='workflow_step'`) followed by the node's actual
output as a normal message. A node that exhausts its retry budget
stops its branch and posts a `system_alert`. If any branch fails (or
trips a loop guard), the whole `WorkflowRun` is marked `failed` even
if siblings completed.

Every run freezes the live graph into
`WorkflowRun.graph_snapshot_json` at start, so a later resume does
not pick up edits made in the builder while the run was paused.

## FR-13.4 — Slash-command triggering

`/{workflow.name} <input>` in a channel message runs that workflow
instead of the normal dispatcher (`api/rivulets.py`'s interceptor,
`workflows/trigger.py:find_triggered_workflow`). Only **published**
workflows match. A message that merely *looks* like a slash command
but doesn't match a published workflow name falls through to ordinary
dispatch unchanged.

## FR-13.5 — Agent-triggered workflows

An agent can launch a workflow programmatically via the `run_workflow`
builtin tool (`tools/builtin/run_workflow.py`). Opt-in per agent
(unlike `handoff`, not attached unconditionally) — detected and
executed the same way handoff calls are, by `dispatch/service.py`
inspecting the completed run's tool calls after the fact.

## FR-13.6 — Sync

`Workflow`, `WorkflowNode`, and `WorkflowConnection` (the definition)
sync across peers exactly like `Agent`/`Team`. `WorkflowRun` /
`WorkflowNodeRun` (execution state) do not. `WorkflowSchedule` and
`WorkflowWebhook` are also local/unsynced: a schedule fires on the
node that owns it, and a webhook URL is only reachable through the
peer whose HTTP port the sender can hit. Duplicate scheduled fires
across peers are avoided by **not syncing schedules**, not by
coordinator election (the election primitive is shipped; this
consumer is not wired — see [`architecture.md`](../architecture.md#coordinator-election-101)).

## Shipped after the original slice

These were the original "Deferred" list. They now ship:

- **Visual canvas (#80, #194).** Drag-and-drop node editor on
  `/workflows/:id`: palette, wiring, positions, and a run that
  animates over the canvas.
- **Branching / parallel / loops (#81).** See FR-13.2.
- **Real `merge` node (#82).** See FR-13.1.
- **Pause / resume (#83, #359).** A `human_input` node sets
  `WorkflowRun.status='awaiting_human'` and `Rivulet.status='paused'`.
  The next human message in that rivulet becomes the node's output
  (`resume_workflow`). Sibling merge arrivals stranded by a
  mid-fan-out pause are persisted
  (`WorkflowRun.pending_merge_arrivals_json`) and folded back into
  the merge on resume. Parallel `human_input` branches resolve one
  reply at a time — the run re-pauses on the next waiting branch
  until every wait is answered. A sibling failure fails any dangling
  waits and releases the rivulet's pause instead of leaving the
  paused banner up over a failed run. Wiring a `workflow` node whose
  child (transitively) contains `human_input` — or adding
  `human_input` to a workflow already embedded as a child — is
  refused at save time; the engine's fail-the-parent behavior remains
  as the backstop for graphs that arrive by sync.
- **Draft vs. published (#84).** A new workflow starts unpublished.
  `published` is a boolean gate on *starting* a new run via slash
  command or `run_workflow`, not a second copy of the graph. Editing
  an already-published workflow takes effect on the next trigger;
  in-flight runs are protected by `graph_snapshot_json`, not by
  publish.
- **Nested workflows (#85).** `node_type='workflow'` +
  `child_workflow_id`. Cycles are refused at run time against the
  executing ancestry set. A completed child's `final_output` becomes
  the parent node's output.
- **Schedules (#92, #93).** Cron (UTC) or one-off `run_once`.
  Agent-created schedules start `enabled=False` until a human
  approves them. Five consecutive fire failures disable the
  schedule. Missed fires are skipped, never backfilled.
- **Webhooks (#99).** HMAC-signed `POST` to the triggering peer.
  Secret shown once at creation.
- **Failure follow-ups (#94).** Optional
  `on_failure_workflow_id` (depth-1 remediation) and
  `on_call_agent_id` (or the workspace default) `@mention` on a
  failed run. Independently configurable; both fire if both are set.

## Still out of scope

- **Coordinator-owned schedule firing.** Election exists;
  `scheduler._tick` does not check `is_self`. Schedules stay
  node-local so they don't double-fire.
- **Propagating a nested pause** up through ancestor runs. Since
  #359 the API refuses to save such a graph instead; the engine
  still fails the parent `workflow` node if one arrives by sync.
- **Standalone FR docs for FR-1–12.** Still comment references only.
