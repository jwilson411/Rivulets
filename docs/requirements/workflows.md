# FR-13: Workflows

Status: **FR-13.1–13.6 shipped** (backend-only slice). FR-13.7+ deferred — see
"Deferred" below. Implements issue #24's first slice, scoped to the backend:
a saved, reusable, node-based automation that a channel can trigger, executed
by a linear engine. No visual canvas ships in this slice.

This is the first FR doc in the repo — FR-1 through FR-12 exist only as
inline references in code comments/docstrings (dispatcher, handoff, sync,
etc.), not as standalone specs. FR-13 starts the convention of writing these
down; back-filling FR-1–12 is a separate, unscoped cleanup, not part of this
work.

## FR-13.1 — Workflow definition

A `Workflow` (`db/models.py`) is a named, saved automation: an ordered graph
of `WorkflowNode`s connected by `WorkflowConnection` edges. `Workflow.name`
doubles as its slash-command trigger (FR-13.4) — one field, not two, since
the two never diverge in this design.

A node is either:
- **`agent`** — invokes an existing `Agent` via the same `agentos.run_agent`
  entry point the dispatcher uses (`workflows/nodes.py:execute_agent_node`).
- **`transform`** — a deterministic string template (`{input}` substitution),
  no LLM call.
- **`summarize`** — an ad-hoc LLM call (no dedicated `Agent` row needed),
  reusing the dispatcher's "pick a cheap model" policy
  (`dispatch/rule_generation.py:pick_dispatcher_model`).
- **`conditional`** — a substring predicate against the input; on failure,
  ends the run early (not a failure — the workflow was designed to stop
  there).
- **`merge`** — currently a pass-through placeholder; real multi-branch
  merge needs parallel execution, out of scope for the linear MVP engine
  (see Deferred).

Each node supports a configurable retry policy (`retry_max_attempts`,
`retry_backoff_seconds`) for transient failures, per issue #24's "Robustness
target" section.

## FR-13.2 — Node graph, linear MVP

`WorkflowConnection` is the source of truth for execution order: an edge
with `from_node_id IS NULL` marks the entry point; every other edge chains
one node's output into the next node's input, unmodified. The MVP engine
(`workflows/engine.py`) walks a single linear chain — it follows the first
outbound edge from each node and does not branch.

The schema itself does **not** enforce linearity (deliberately, per issue
#24's "data model must not foreclose branching later") — `WorkflowConnection`
already supports multiple outbound edges per node and carries a reserved
`condition_json` column for a future branching engine to pick among them.
The linear invariant (at most one outbound edge per node, at most one entry
point) is enforced only at the API layer (`api/workflows.py`, 409 on
violation), so relaxing it later is an API/engine change, not a migration.

## FR-13.3 — Execution & telemetry

Running a workflow produces a `WorkflowRun` (one row, `status`:
running/completed/failed/awaiting_human) and one `WorkflowNodeRun` per node
execution attempt (one row per retry attempt, not one row updated in place,
so retry history stays inspectable). Both are **local execution state, not
synced** — same treatment as `AgentRun`/`DispatchDecision`: a fresh peer
doesn't need another node's run history to function, only the workflow
*definition* needs to replicate.

Each node's execution posts a visible step-indicator message into the
rivulet (`content_type='workflow_step'`, the same "visual divider" pattern
FR-6.3's handoff uses) followed by the node's actual output as a normal
message — the engine's answer to issue #24's open question on step
visibility, picked as the more transparent default. A node that exhausts
its retry budget stops the whole run and posts a `system_alert`; control
returns to the human, no automatic escalation (issue #24's stated default
failure behavior).

## FR-13.4 — Slash-command triggering

`/{workflow.name} <input>` in a channel message runs that workflow instead
of the normal dispatcher (`api/rivulets.py`'s interceptor,
`workflows/trigger.py:find_triggered_workflow`). A message that merely
*looks* like a slash command but doesn't match a registered workflow name
falls through to ordinary dispatch unchanged — this is deliberately
permissive so existing messages starting with `/` (file paths, code
snippets) aren't silently swallowed by workflow matching.

## FR-13.5 — Agent-triggered workflows

An agent can launch a workflow programmatically via the `run_workflow`
builtin tool (`tools/builtin/run_workflow.py`), satisfying issue #24's "a
human typing `@some-agent run this workflow` should let that agent launch
it, not just a human typing the slash command" requirement. Opt-in per
agent (unlike `handoff`, not attached unconditionally) — detected and
executed the same way handoff calls are, by `dispatch/service.py` inspecting
the completed run's tool calls after the fact
(`_find_run_workflow_call`/`_handle_run_workflow_trigger`).

## FR-13.6 — Sync

`Workflow`, `WorkflowNode`, and `WorkflowConnection` (the definition) sync
across peers exactly like `Agent`/`Team` (`sync/apply.py`'s
`WORKFLOW_SPEC`/`WORKFLOW_NODE_SPEC`/`WORKFLOW_CONNECTION_SPEC`). `WorkflowRun`/
`WorkflowNodeRun` (execution state) do not sync, per FR-13.3.

## Deferred (not in this slice)

Everything below is still open, matching issue #24's own "Open questions" —
this FR doc doesn't resolve them, it records what's shipped so the gap is
explicit:

- **Visual canvas.** No builder UI ships here — workflows are authored as
  plain JSON against `api/workflows.py`. The n8n-style drag-and-drop editor
  issue #24 describes is a separate, larger UI effort.
- **Branching/conditionals/loops/parallel execution.** The schema is
  designed not to need a rewrite (FR-13.2), but the engine itself is
  linear-only today.
- **Real `merge` node behavior.** Needs parallel branch execution first.
- **Pause/resume for human input mid-run.** Not implemented; a failed node
  stops the run entirely rather than pausing for input at a specific node.
- **Draft vs. published versioning.** Editing a workflow definition takes
  effect immediately; there's no in-flight-run protection against a
  concurrent edit.
- **Nested workflows** (one workflow invoking another as a node). Not
  implemented — `node_type` has no `workflow` option yet.
