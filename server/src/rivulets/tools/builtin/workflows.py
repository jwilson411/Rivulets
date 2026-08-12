"""Workflow definition management tools (#192): create/update/delete/
publish/unpublish/list built-in tools mirroring the workflow-level subset
of api/workflows.py's HTTP surface, so an agent holding the
"workflows:manage" scope (#188, agentos/tool_scopes.py) can author and
publish saved workflows the same way a human can through the API --
another slice of #125's "act on my behalf" tracking issue, alongside
#189's channel tools, #190's agent/team tools, and #191's MCP server
tools.

Same deliberately side-effect-free shape as channels.py/agents_teams.py/
mcp_servers.py: none of these functions touch the DB directly -- see
agentos/tool_resolution.py's _BUILTIN_REGISTRY docstring for why a shared
`@tool` callable can't have a request-scoped session. The real work
happens in dispatch/service.py's _handle_create_workflow_trigger /
_handle_update_workflow_trigger / _handle_delete_workflow_trigger /
_handle_publish_workflow_trigger / _handle_unpublish_workflow_trigger /
_handle_list_workflows_trigger, which inspect the completed run's tool
calls the same way it already does for create_channel/create_agent/
register_mcp_server.

Deliberately narrower than api/workflows.py's full surface -- #192's issue
body flags the node/connection/schedule/webhook graph-editing surface
(create_node, create_connection, create_schedule, create_webhook, and
their update/delete/list counterparts) as "the largest sub-surface in this
issue" and suggests splitting it into its own follow-up once scoped, since
authoring a graph one node/edge at a time through tool calls is a very
different (and much larger) design problem than the six workflow-level
CRUD operations below. This module only covers the latter: creating a
still-empty draft, renaming/describing it, publishing/unpublishing it, and
deleting it -- the same "definition CRUD" scope run_workflow.py already
assumes exists before it can trigger anything by name.

`on_failure_workflow_id`/`on_call_agent_id` (#94's auto-remediation/
on-call layers) are also left out of update_workflow's parameters here,
on purpose: both are advanced, rarely-touched configuration for a specific
observability feature, not part of the core "create and publish a
workflow" loop this tool set targets, and every other field on
WorkflowUpdate already round-trips cleanly through the simpler shape
below -- they can be added as their own tool (or update_workflow
parameters) later if an agent-authored use case for them shows up,
without any migration or scope change (workflows:manage already covers
whatever workflow-level tool needs it).
"""

from agno.tools import tool


@tool
def create_workflow(name: str, description: str = "") -> str:
    """Create a new, empty workflow definition. `name` must be 2-64
    characters, lowercase letters/digits/hyphens only, starting with a
    letter (it doubles as the `/{name}` slash command once published),
    and not already in use by another workflow. `description` is
    optional. The new workflow starts unpublished with no steps -- use
    the workflow canvas (or the node/connection API) to add steps, then
    publish_workflow once it has at least one connected step."""
    return f"Requested creation of workflow {name!r}."


@tool
def update_workflow(workflow: str, name: str | None = None, description: str | None = None) -> str:
    """Rename and/or change the description of an existing workflow.
    `workflow` is the workflow's id or its current name. Only the fields
    you provide are changed; omit `name` or `description` to leave it
    as-is. Renaming doesn't affect a published workflow's ability to run,
    but any `/{old-name}` slash command stops matching once the rename
    takes effect."""
    return f"Requested update to workflow {workflow!r}."


@tool
def delete_workflow(workflow: str) -> str:
    """Permanently delete a workflow definition, along with its nodes and
    connections. `workflow` is the workflow's id or its current name.
    This cannot be undone -- any schedule or webhook still pointing at it
    stops firing, and any node_type='workflow' step elsewhere that
    embedded it as a child now references a workflow that no longer
    exists."""
    return f"Requested deletion of workflow {workflow!r}."


@tool
def publish_workflow(workflow: str) -> str:
    """Publish a workflow, letting it be triggered via `/{name}` in a
    channel or via the run_workflow tool. `workflow` is the workflow's id
    or its current name. Fails if the workflow has no entry point yet
    (connect a first step before publishing)."""
    return f"Requested publishing of workflow {workflow!r}."


@tool
def unpublish_workflow(workflow: str) -> str:
    """Revert a workflow to draft, so `/{name}` and the run_workflow tool
    stop matching it. `workflow` is the workflow's id or its current
    name. Has no effect on any run already in flight."""
    return f"Requested unpublishing of workflow {workflow!r}."


@tool
def list_workflows() -> str:
    """List the workspace's workflows, including which are published, so
    you can find a workflow's id/name before acting on it with the other
    workflow tools."""
    return "Looking up the workspace's workflows."
