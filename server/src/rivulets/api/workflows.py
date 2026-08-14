"""Workflow definitions (#24): saved, reusable, node-based automations
that a channel can trigger via `/​{workflow.name} <input>`
(api/rivulets.py's slash-command interceptor) or an agent can trigger via
tools/builtin/run_workflow.py.

This is the definition CRUD surface only — no visual canvas ships in this
slice (the issue's builder is a separate, larger UI effort); workflows are
authored as plain JSON against this API for now. `Workflow`/`WorkflowNode`/
`WorkflowConnection` sync across peers like Agent/Team (sync/apply.py);
`WorkflowRun`/`WorkflowNodeRun` (execution state) are read-only here,
local to whichever node actually ran them — see db/models.py's docstrings.

The engine (workflows/engine.py, branching/parallel/loops via #81) now
walks a real graph, not just a single chain, so this module enforces only
one invariant the schema itself doesn't: a workflow has at most one entry
connection (`from_node_id IS NULL`) — still a 409, a conflict with an
existing connection, not malformed input. A node may have any number of
outbound connections; `condition_json` on each (validated by
`_validate_condition` below) decides whether the engine follows it — see
workflows/engine.py's module docstring for the predicate shape and how
multiple matching edges fan out.

`publish_workflow`/`unpublish_workflow` (#84) flip `Workflow.published`,
the gate on whether `/{name}` or the run_workflow tool can start a new
run — see Workflow's own docstring for exactly what that does and
doesn't protect. Editing nodes/connections isn't otherwise restricted by
publish state; a published workflow can still be freely edited here,
same as before this existed.

A node_type='workflow' node's `child_workflow_id` (#85) only gets the
cheap, always-correct check at save time -- rejecting a direct
self-reference (`_validate_child_workflow`). A multi-hop cycle (workflow
A embeds B, B embeds A) can't be fully ruled out here since it can appear
later purely by editing a *different* workflow, so it's caught instead by
workflows/engine.py's runtime ancestry check when the workflow actually
runs, not by this endpoint — see that module's docstring.

`list_failed_runs` (#94 layer 1, observability) is the one endpoint here
that isn't scoped to a single `workflow_id`: a failed run was previously
only discoverable per-workflow via `list_runs`, with no way to see
"what's failing right now" across all of them.

`Workflow.on_failure_workflow_id` (#94 layer 2, auto-remediation) and
`on_call_agent_id` (#94 layer 3, on-call `@mention`) are the two fields
on `WorkflowUpdate` that can be explicitly cleared back to null via
PATCH -- `update_workflow` checks `body.model_fields_set` for both
rather than the `is not None` shortcut every other field here uses,
since each, once configured, needs a way to be turned back off.
`_validate_on_failure_workflow` only checks existence, not
self-reference, unlike `_validate_child_workflow` -- see
Workflow.on_failure_workflow_id's own docstring for why a self-reference
here is a legitimate "retry once" shape rather than a mistake.
`_validate_on_call_agent` is a plain existence check, same as any other
entity-reference field here -- the workspace-wide fallback
('workflows.default_on_call_agent_id', api/settings.py) is the one
place an on-call agent id goes unvalidated, since every other key in
that generic settings table is opaque JSON already.
"""

import json
import logging

from croniter import CroniterError
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.models import (
    Agent,
    Channel,
    Rivulet,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowSchedule,
    WorkflowWebhook,
)
from rivulets.security import keys
from rivulets.security.session import get_session_key_store
from rivulets.security.webhook_secret_store import encrypt_webhook_secret
from rivulets.sync.publish import publish_current_state, publish_tombstone
from rivulets.workflows.layout import auto_layout
from rivulets.workflows.nodes import NODE_TYPES
from rivulets.workflows.scheduler import compute_next_fire_at

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

# Slash-command-safe: what api/rivulets.py's interceptor will match after
# the leading '/'. Lowercase to avoid a workflow named "Foo" being
# unreachable via a human typing "/foo" (slash commands are matched
# case-insensitively there, but the stored name itself is kept canonical).
_NAME_PATTERN = r"^[a-z][a-z0-9-]{1,63}$"


class WorkflowCreate(BaseModel):
    name: str = Field(pattern=_NAME_PATTERN)
    description: str | None = None


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, pattern=_NAME_PATTERN)
    description: str | None = None
    # #94 layers 2/3: unlike every other field here, these two need to be
    # clearable back to null -- `update_workflow` checks `model_fields_set`
    # for both instead of the `is not None` shortcut every other field
    # uses, since `None` is a meaningful, settable value here rather than
    # only meaning "not provided".
    on_failure_workflow_id: str | None = None
    on_call_agent_id: str | None = None


class WorkflowOut(BaseModel):
    id: str
    name: str
    description: str | None
    published: bool
    on_failure_workflow_id: str | None
    on_call_agent_id: str | None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class WorkflowNodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    node_type: str
    agent_id: str | None = None
    child_workflow_id: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    retry_max_attempts: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: int = Field(default=5, ge=0, le=3600)
    position_x: float | None = None
    position_y: float | None = None


class WorkflowNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    agent_id: str | None = None
    child_workflow_id: str | None = None
    config: dict[str, object] | None = None
    retry_max_attempts: int | None = Field(default=None, ge=0, le=10)
    retry_backoff_seconds: int | None = Field(default=None, ge=0, le=3600)
    position_x: float | None = None
    position_y: float | None = None


class WorkflowNodeOut(BaseModel):
    id: str
    workflow_id: str
    name: str
    node_type: str
    agent_id: str | None
    child_workflow_id: str | None
    config: dict[str, object]
    retry_max_attempts: int
    retry_backoff_seconds: int
    position_x: float | None
    position_y: float | None

    @classmethod
    def from_row(cls, row: WorkflowNode) -> "WorkflowNodeOut":
        return cls(
            id=row.id,
            workflow_id=row.workflow_id,
            name=row.name,
            node_type=row.node_type,
            agent_id=row.agent_id,
            child_workflow_id=row.child_workflow_id,
            config=json.loads(row.config_json) if row.config_json else {},
            retry_max_attempts=row.retry_max_attempts,
            retry_backoff_seconds=row.retry_backoff_seconds,
            position_x=row.position_x,
            position_y=row.position_y,
        )


class WorkflowConnectionCreate(BaseModel):
    from_node_id: str | None = None  # None = this workflow's entry point
    to_node_id: str
    # None = always follow this edge. Otherwise exactly one of
    # {"contains": "text"} / {"not_contains": "text"} — see
    # workflows/engine.py's module docstring.
    condition_json: dict[str, object] | None = None


class WorkflowConnectionUpdate(BaseModel):
    """#198: the canvas's edge inspector needs to change (or clear) an
    existing connection's condition_json without recreating the edge, so
    this checks `model_fields_set` the same way WorkflowUpdate does for
    on_failure_workflow_id — an explicit `null` means "clear the
    condition", not "leave it alone", which the `is not None` shortcut
    used elsewhere in this module can't distinguish."""

    condition_json: dict[str, object] | None = None


class WorkflowConnectionOut(BaseModel):
    id: str
    workflow_id: str
    from_node_id: str | None
    to_node_id: str
    condition_json: dict[str, object] | None

    @classmethod
    def from_row(cls, row: WorkflowConnection) -> "WorkflowConnectionOut":
        return cls(
            id=row.id,
            workflow_id=row.workflow_id,
            from_node_id=row.from_node_id,
            to_node_id=row.to_node_id,
            condition_json=json.loads(row.condition_json) if row.condition_json else None,
        )


def _validate_condition(condition: dict[str, object] | None) -> None:
    if condition is None:
        return
    keys = set(condition)
    if keys == {"contains"} and isinstance(condition["contains"], str) and condition["contains"]:
        return
    if (
        keys == {"not_contains"}
        and isinstance(condition["not_contains"], str)
        and condition["not_contains"]
    ):
        return
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "condition_json must be omitted or exactly one of "
        '{"contains": "text"} / {"not_contains": "text"}',
    )


class WorkflowRunOut(BaseModel):
    id: str
    workflow_id: str
    rivulet_id: str
    triggered_by: str
    triggered_by_id: str | None
    status: str
    current_node_id: str | None
    error_message: str | None
    final_output: str | None
    started_at: str
    completed_at: str | None

    model_config = {"from_attributes": True}


class FailedWorkflowRunOut(WorkflowRunOut):
    """A WorkflowRunOut plus the two things a cross-workflow failure list
    (#94's "make failures actually observable" layer) needs that the
    per-workflow /runs endpoint doesn't, since the workflow is implied by
    the URL there: which workflow this run belongs to, and which channel
    to jump into (via rivulet_id) to see what actually happened."""

    workflow_name: str
    channel_id: str


class WorkflowNodeRunOut(BaseModel):
    id: str
    node_id: str
    attempt: int
    status: str
    output_content: str | None
    error_message: str | None
    started_at: str
    completed_at: str | None

    model_config = {"from_attributes": True}


class WorkflowScheduleCreate(BaseModel):
    channel_id: str
    cron_expression: str = Field(min_length=1, max_length=128)
    input_content: str = Field(default="", max_length=4000)
    enabled: bool = True


class WorkflowScheduleUpdate(BaseModel):
    channel_id: str | None = None
    cron_expression: str | None = Field(default=None, min_length=1, max_length=128)
    input_content: str | None = None
    enabled: bool | None = None


class WorkflowScheduleOut(BaseModel):
    id: str
    workflow_id: str
    channel_id: str
    cron_expression: str | None
    run_once: bool
    input_content: str
    enabled: bool
    next_fire_at: str
    last_fired_at: str | None
    consecutive_failures: int
    name: str | None
    created_by: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class CronPreviewRequest(BaseModel):
    cron_expression: str = Field(min_length=1, max_length=128)


class CronPreviewOut(BaseModel):
    valid: bool
    next_fire_at: str | None
    error: str | None


class WorkflowWebhookCreate(BaseModel):
    channel_id: str
    name: str | None = None
    # "{input}" substitution, same convention as a transform node's
    # template (workflows/nodes.py) -- None passes the raw request body
    # through unchanged.
    input_template: str | None = Field(default=None, max_length=4000)
    enabled: bool = True


class WorkflowWebhookUpdate(BaseModel):
    channel_id: str | None = None
    name: str | None = None
    input_template: str | None = None
    enabled: bool | None = None


class WorkflowWebhookOut(BaseModel):
    id: str
    workflow_id: str
    channel_id: str
    name: str | None
    input_template: str | None
    enabled: bool
    last_triggered_at: str | None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class WorkflowWebhookCreated(WorkflowWebhookOut):
    """Returned only from create_webhook (and rotate_webhook_secret) --
    the plaintext secret is shown exactly once, the same shown-once UX as
    an invite link (api/invites.py's create_invite) and the workspace
    mnemonic itself. It's never persisted or returned again; only its
    encrypted form (WorkflowWebhook.secret_ciphertext) lives in the DB."""

    secret: str


async def _get_workflow_or_404(db: DbSession, workflow_id: str) -> Workflow:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return workflow


async def _get_node_or_404(db: DbSession, workflow_id: str, node_id: str) -> WorkflowNode:
    node = await db.get(WorkflowNode, node_id)
    if node is None or node.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow node not found")
    return node


async def _get_connection_or_404(
    db: DbSession, workflow_id: str, connection_id: str
) -> WorkflowConnection:
    connection = await db.get(WorkflowConnection, connection_id)
    if connection is None or connection.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow connection not found")
    return connection


async def _get_schedule_or_404(
    db: DbSession, workflow_id: str, schedule_id: str
) -> WorkflowSchedule:
    schedule = await db.get(WorkflowSchedule, schedule_id)
    if schedule is None or schedule.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow schedule not found")
    return schedule


async def _get_webhook_or_404(db: DbSession, workflow_id: str, webhook_id: str) -> WorkflowWebhook:
    webhook = await db.get(WorkflowWebhook, webhook_id)
    if webhook is None or webhook.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow webhook not found")
    return webhook


def _compute_next_fire_at_or_400(cron_expression: str) -> str:
    try:
        return compute_next_fire_at(cron_expression)
    except CroniterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid cron expression: {exc}") from exc


async def _validate_child_workflow(db: DbSession, workflow_id: str, child_workflow_id: str) -> None:
    """#85: only the cheap, always-correct static check -- a direct
    self-reference (a workflow embedding itself). A multi-hop cycle
    (A embeds B, B embeds A) can't be fully ruled out here: it can appear
    later purely by editing a *different* workflow's nodes, so it's the
    runtime ancestry check in workflows/engine.py's _execute_workflow_node
    that actually guarantees safety, not this endpoint -- see that
    module's docstring."""
    if child_workflow_id == workflow_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A workflow can't embed itself as a step")
    if await db.get(Workflow, child_workflow_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")


async def _validate_on_failure_workflow(db: DbSession, on_failure_workflow_id: str) -> None:
    """#94 layer 2: existence only -- unlike `_validate_child_workflow`, a
    self-reference is deliberately *not* rejected here (see
    Workflow.on_failure_workflow_id's docstring for why "retry this same
    workflow once on failure" is safe without that check)."""
    if await db.get(Workflow, on_failure_workflow_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Remediation workflow not found")


async def _validate_on_call_agent(db: DbSession, on_call_agent_id: str) -> None:
    """#94 layer 3: existence only. Unlike the workspace-wide
    'workflows.default_on_call_agent_id' setting (api/settings.py,
    unvalidated like every other row in that table), this is a real
    dedicated FK column, so it gets the same existence check every other
    entity-reference field in this file does."""
    if await db.get(Agent, on_call_agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(body: WorkflowCreate, db: DbSession, _: CurrentWorkspaceId) -> Workflow:
    existing = await db.scalar(select(Workflow).where(Workflow.name == body.name))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A workflow named {body.name!r} already exists"
        )
    workflow = Workflow(name=body.name, description=body.description)
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    await publish_current_state(db, "workflow", workflow.id)
    return workflow


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(db: DbSession, _: CurrentWorkspaceId) -> list[Workflow]:
    result = await db.execute(select(Workflow).order_by(Workflow.name))
    return list(result.scalars().all())


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: str, db: DbSession, _: CurrentWorkspaceId) -> Workflow:
    return await _get_workflow_or_404(db, workflow_id)


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str, body: WorkflowUpdate, db: DbSession, _: CurrentWorkspaceId
) -> Workflow:
    workflow = await _get_workflow_or_404(db, workflow_id)
    if body.name is not None and body.name != workflow.name:
        existing = await db.scalar(select(Workflow).where(Workflow.name == body.name))
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"A workflow named {body.name!r} already exists"
            )
        workflow.name = body.name
    if body.description is not None:
        workflow.description = body.description
    if "on_failure_workflow_id" in body.model_fields_set:
        if body.on_failure_workflow_id is not None:
            await _validate_on_failure_workflow(db, body.on_failure_workflow_id)
        workflow.on_failure_workflow_id = body.on_failure_workflow_id
    if "on_call_agent_id" in body.model_fields_set:
        if body.on_call_agent_id is not None:
            await _validate_on_call_agent(db, body.on_call_agent_id)
        workflow.on_call_agent_id = body.on_call_agent_id
    await db.commit()
    await db.refresh(workflow)
    await publish_current_state(db, "workflow", workflow.id)
    return workflow


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    workflow = await _get_workflow_or_404(db, workflow_id)
    await db.delete(workflow)
    await db.commit()
    # #287: same #238 failure mode publish_tombstone already closed for
    # delete_agent/delete_team -- without it a peer that still has this
    # workflow would recreate it on its next edit. Its nodes/connections
    # don't need their own tombstones here: WorkflowNode/WorkflowConnection
    # both have a real ondelete='CASCADE' FK to workflow.id (db/models.py),
    # so a peer applying *this* tombstone (apply_remote_delete's db.delete)
    # has its own copies of them cascade-deleted at the SQLite level too.
    await publish_tombstone(db, "workflow", workflow_id)


@router.post("/{workflow_id}/publish", response_model=WorkflowOut)
async def publish_workflow(workflow_id: str, db: DbSession, _: CurrentWorkspaceId) -> Workflow:
    """#84: only a published workflow can be triggered via `/{name}` or
    the run_workflow tool (workflows/trigger.py's find_workflow_by_name)
    -- see Workflow's docstring for what this does and doesn't guarantee.
    Refuses (400) without an entry connection, the same "can this even
    run" check the engine itself makes at trigger time -- publishing is
    meant to mean "ready", not just "flagged"."""
    workflow = await _get_workflow_or_404(db, workflow_id)
    entry = await db.scalar(
        select(WorkflowConnection).where(
            WorkflowConnection.workflow_id == workflow_id,
            WorkflowConnection.from_node_id.is_(None),
        )
    )
    if entry is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Workflow has no entry point yet — connect a first step before publishing",
        )
    workflow.published = True
    await db.commit()
    await db.refresh(workflow)
    await publish_current_state(db, "workflow", workflow.id)
    return workflow


@router.post("/{workflow_id}/unpublish", response_model=WorkflowOut)
async def unpublish_workflow(workflow_id: str, db: DbSession, _: CurrentWorkspaceId) -> Workflow:
    """Reverts to draft -- new triggers stop matching this workflow's name
    (find_workflow_by_name), but this has no effect on any WorkflowRun
    already in flight (workflows/engine.py's graph_snapshot_json is what
    protects those, not `published`)."""
    workflow = await _get_workflow_or_404(db, workflow_id)
    workflow.published = False
    await db.commit()
    await db.refresh(workflow)
    await publish_current_state(db, "workflow", workflow.id)
    return workflow


@router.post(
    "/{workflow_id}/nodes", response_model=WorkflowNodeOut, status_code=status.HTTP_201_CREATED
)
async def create_node(
    workflow_id: str, body: WorkflowNodeCreate, db: DbSession, _: CurrentWorkspaceId
) -> WorkflowNodeOut:
    await _get_workflow_or_404(db, workflow_id)
    if body.node_type not in NODE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown node_type {body.node_type!r}")
    if body.node_type == "agent":
        if body.agent_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "agent_id is required for node_type='agent'"
            )
        if await db.get(Agent, body.agent_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if body.node_type == "workflow":
        if body.child_workflow_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "child_workflow_id is required for node_type='workflow'",
            )
        await _validate_child_workflow(db, workflow_id, body.child_workflow_id)
    node = WorkflowNode(
        workflow_id=workflow_id,
        name=body.name,
        node_type=body.node_type,
        agent_id=body.agent_id if body.node_type == "agent" else None,
        child_workflow_id=body.child_workflow_id if body.node_type == "workflow" else None,
        config_json=json.dumps(body.config) if body.config else None,
        retry_max_attempts=body.retry_max_attempts,
        retry_backoff_seconds=body.retry_backoff_seconds,
        position_x=body.position_x,
        position_y=body.position_y,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    await publish_current_state(db, "workflow_node", node.id)
    return WorkflowNodeOut.from_row(node)


@router.get("/{workflow_id}/nodes", response_model=list[WorkflowNodeOut])
async def list_nodes(
    workflow_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[WorkflowNodeOut]:
    await _get_workflow_or_404(db, workflow_id)
    result = await db.execute(
        select(WorkflowNode)
        .where(WorkflowNode.workflow_id == workflow_id)
        .order_by(WorkflowNode.created_at)
    )
    out = [WorkflowNodeOut.from_row(row) for row in result.scalars().all()]
    # #194: workflows saved before the canvas existed (or nodes created
    # without an explicit position) have position_x/position_y = None.
    # Fill those in from the auto-layout fallback rather than persisting
    # anything here -- a GET shouldn't have a write side effect, and this
    # is deterministic from the graph shape so recomputing on each read
    # costs nothing and stays consistent.
    if any(node.position_x is None or node.position_y is None for node in out):
        connections_result = await db.execute(
            select(WorkflowConnection).where(WorkflowConnection.workflow_id == workflow_id)
        )
        edges = [(c.from_node_id, c.to_node_id) for c in connections_result.scalars().all()]
        computed = auto_layout([node.id for node in out], edges)
        for node in out:
            if node.position_x is None or node.position_y is None:
                node.position_x, node.position_y = computed[node.id]
    return out


@router.patch("/{workflow_id}/nodes/{node_id}", response_model=WorkflowNodeOut)
async def update_node(
    workflow_id: str, node_id: str, body: WorkflowNodeUpdate, db: DbSession, _: CurrentWorkspaceId
) -> WorkflowNodeOut:
    node = await _get_node_or_404(db, workflow_id, node_id)
    if body.name is not None:
        node.name = body.name
    if body.agent_id is not None:
        if node.node_type != "agent":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "agent_id only applies to node_type='agent'"
            )
        if await db.get(Agent, body.agent_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
        node.agent_id = body.agent_id
    if body.child_workflow_id is not None:
        if node.node_type != "workflow":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "child_workflow_id only applies to node_type='workflow'",
            )
        await _validate_child_workflow(db, workflow_id, body.child_workflow_id)
        node.child_workflow_id = body.child_workflow_id
    if body.config is not None:
        node.config_json = json.dumps(body.config) if body.config else None
    if body.retry_max_attempts is not None:
        node.retry_max_attempts = body.retry_max_attempts
    if body.retry_backoff_seconds is not None:
        node.retry_backoff_seconds = body.retry_backoff_seconds
    if body.position_x is not None:
        node.position_x = body.position_x
    if body.position_y is not None:
        node.position_y = body.position_y
    await db.commit()
    await db.refresh(node)
    await publish_current_state(db, "workflow_node", node.id)
    return WorkflowNodeOut.from_row(node)


@router.delete("/{workflow_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(workflow_id: str, node_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    node = await _get_node_or_404(db, workflow_id, node_id)
    await db.delete(node)
    await db.commit()
    # #287: the workflow itself isn't being deleted here, so unlike
    # delete_workflow above there's no parent tombstone that will cascade
    # this node away on a peer -- it needs its own.
    await publish_tombstone(db, "workflow_node", node_id)


@router.post(
    "/{workflow_id}/connections",
    response_model=WorkflowConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    workflow_id: str, body: WorkflowConnectionCreate, db: DbSession, _: CurrentWorkspaceId
) -> WorkflowConnectionOut:
    await _get_workflow_or_404(db, workflow_id)
    if body.from_node_id is not None:
        await _get_node_or_404(db, workflow_id, body.from_node_id)
    await _get_node_or_404(db, workflow_id, body.to_node_id)
    _validate_condition(body.condition_json)

    if body.from_node_id is None:
        existing_entry = await db.scalar(
            select(WorkflowConnection).where(
                WorkflowConnection.workflow_id == workflow_id,
                WorkflowConnection.from_node_id.is_(None),
            )
        )
        if existing_entry is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "the workflow's entry point already has an outbound connection — a workflow "
                "starts at exactly one node",
            )

    connection = WorkflowConnection(
        workflow_id=workflow_id,
        from_node_id=body.from_node_id,
        to_node_id=body.to_node_id,
        condition_json=json.dumps(body.condition_json) if body.condition_json else None,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    await publish_current_state(db, "workflow_connection", connection.id)
    return WorkflowConnectionOut.from_row(connection)


@router.get("/{workflow_id}/connections", response_model=list[WorkflowConnectionOut])
async def list_connections(
    workflow_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[WorkflowConnectionOut]:
    await _get_workflow_or_404(db, workflow_id)
    result = await db.execute(
        select(WorkflowConnection).where(WorkflowConnection.workflow_id == workflow_id)
    )
    return [WorkflowConnectionOut.from_row(row) for row in result.scalars().all()]


@router.patch("/{workflow_id}/connections/{connection_id}", response_model=WorkflowConnectionOut)
async def update_connection(
    workflow_id: str,
    connection_id: str,
    body: WorkflowConnectionUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
) -> WorkflowConnectionOut:
    connection = await _get_connection_or_404(db, workflow_id, connection_id)
    if "condition_json" in body.model_fields_set:
        _validate_condition(body.condition_json)
        connection.condition_json = json.dumps(body.condition_json) if body.condition_json else None
    await db.commit()
    await db.refresh(connection)
    await publish_current_state(db, "workflow_connection", connection.id)
    return WorkflowConnectionOut.from_row(connection)


@router.delete("/{workflow_id}/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    workflow_id: str, connection_id: str, db: DbSession, _: CurrentWorkspaceId
) -> None:
    connection = await _get_connection_or_404(db, workflow_id, connection_id)
    await db.delete(connection)
    await db.commit()
    # #287: same reasoning as delete_node above -- a lone connection delete
    # has no parent-delete tombstone to ride along with.
    await publish_tombstone(db, "workflow_connection", connection_id)


@router.post(
    "/{workflow_id}/schedules",
    response_model=WorkflowScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    workflow_id: str, body: WorkflowScheduleCreate, db: DbSession, _: CurrentWorkspaceId
) -> WorkflowSchedule:
    """#92: publish-state is deliberately NOT checked here -- same as
    nodes, a schedule can be configured against a still-draft workflow;
    only workflows/scheduler.py's _fire re-checks Workflow.published at
    fire time, the single gate every trigger path shares."""
    await _get_workflow_or_404(db, workflow_id)
    if await db.get(Channel, body.channel_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    next_fire_at = _compute_next_fire_at_or_400(body.cron_expression)
    schedule = WorkflowSchedule(
        workflow_id=workflow_id,
        channel_id=body.channel_id,
        cron_expression=body.cron_expression,
        input_content=body.input_content,
        enabled=body.enabled,
        next_fire_at=next_fire_at,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    # Deliberately no publish_current_state call -- WorkflowSchedule is
    # unsynced (see its own docstring / sync/apply.py's absence of it
    # from _DISPATCH).
    return schedule


@router.get("/{workflow_id}/schedules", response_model=list[WorkflowScheduleOut])
async def list_schedules(
    workflow_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[WorkflowSchedule]:
    await _get_workflow_or_404(db, workflow_id)
    result = await db.execute(
        select(WorkflowSchedule)
        .where(WorkflowSchedule.workflow_id == workflow_id)
        .order_by(WorkflowSchedule.created_at)
    )
    return list(result.scalars().all())


@router.patch("/{workflow_id}/schedules/{schedule_id}", response_model=WorkflowScheduleOut)
async def update_schedule(
    workflow_id: str,
    schedule_id: str,
    body: WorkflowScheduleUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
) -> WorkflowSchedule:
    schedule = await _get_schedule_or_404(db, workflow_id, schedule_id)
    if body.channel_id is not None:
        if await db.get(Channel, body.channel_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
        schedule.channel_id = body.channel_id
    if body.cron_expression is not None:
        schedule.cron_expression = body.cron_expression
        schedule.next_fire_at = _compute_next_fire_at_or_400(body.cron_expression)
    if body.input_content is not None:
        schedule.input_content = body.input_content
    if body.enabled is not None:
        # Re-enabling recomputes next_fire_at from *now* (not whatever
        # stale value it had while disabled) and clears the failure
        # streak -- a manual re-enable is an implicit "I fixed it, try
        # again clean" signal. A #93 one-off (run_once) schedule has no
        # cron_expression to recompute from -- its next_fire_at is the
        # specific timestamp it was created with (e.g. a human approving
        # an agent-created "remind me in 20 minutes"), so re-enabling
        # leaves it untouched instead of crashing on a None cron *as long
        # as it hasn't fired yet* -- once it has (last_fired_at is set),
        # that stale next_fire_at is necessarily in the past, and
        # re-enabling would make the scheduler fire the same one-off
        # reminder again on the very next poll tick. A one-off is done
        # once it's fired; re-running it needs a new schedule, not a
        # re-enable.
        if body.enabled and not schedule.enabled:
            if schedule.run_once and schedule.last_fired_at is not None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "This one-time schedule already fired and can't be re-enabled — "
                    "create a new schedule instead",
                )
            if schedule.cron_expression is not None:
                schedule.next_fire_at = _compute_next_fire_at_or_400(schedule.cron_expression)
            schedule.consecutive_failures = 0
        schedule.enabled = body.enabled
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.delete("/{workflow_id}/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    workflow_id: str, schedule_id: str, db: DbSession, _: CurrentWorkspaceId
) -> None:
    schedule = await _get_schedule_or_404(db, workflow_id, schedule_id)
    await db.delete(schedule)
    await db.commit()


@router.post("/{workflow_id}/schedules/preview", response_model=CronPreviewOut)
async def preview_schedule(
    workflow_id: str, body: CronPreviewRequest, db: DbSession, _: CurrentWorkspaceId
) -> CronPreviewOut:
    """No persistence -- lets the UI show a human-readable "next run" (or
    a validation error) while someone is still typing a cron expression,
    before they click Save."""
    await _get_workflow_or_404(db, workflow_id)
    try:
        return CronPreviewOut(
            valid=True, next_fire_at=compute_next_fire_at(body.cron_expression), error=None
        )
    except CroniterError as exc:
        return CronPreviewOut(valid=False, next_fire_at=None, error=str(exc))


@router.post(
    "/{workflow_id}/webhooks",
    response_model=WorkflowWebhookCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook(
    workflow_id: str,
    body: WorkflowWebhookCreate,
    db: DbSession,
    _: CurrentWorkspaceId,
    _o: OwnerGrant,
) -> WorkflowWebhookCreated:
    """#99: publish-state is deliberately NOT checked here, same as
    create_schedule above -- a webhook can be configured against a
    still-draft workflow; only api/webhooks.py's trigger endpoint
    re-checks Workflow.published at invocation time, the single gate
    every trigger path shares (workflows/trigger.py).

    #242: owner-gated, same bucket as invite management -- create/rotate
    mint the HMAC secret that *is* the trigger credential (api/webhooks.py
    is deliberately unauthenticated beyond that HMAC), so an invite-grant
    session minting one for itself would hand it a durable, unattended
    external trigger into the workspace that outlives the session."""
    await _get_workflow_or_404(db, workflow_id)
    if await db.get(Channel, body.channel_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")

    secret = keys.generate_webhook_secret()
    encryption_key = get_session_key_store().get_webhook_secret_key()
    nonce, ciphertext = encrypt_webhook_secret(secret, encryption_key)
    webhook = WorkflowWebhook(
        workflow_id=workflow_id,
        channel_id=body.channel_id,
        name=body.name,
        input_template=body.input_template,
        enabled=body.enabled,
        secret_nonce=nonce,
        secret_ciphertext=ciphertext,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    # Deliberately no publish_current_state call -- WorkflowWebhook is
    # unsynced (see its own docstring / sync/apply.py's absence of it
    # from _DISPATCH).
    return WorkflowWebhookCreated(
        **WorkflowWebhookOut.model_validate(webhook).model_dump(), secret=secret
    )


@router.get("/{workflow_id}/webhooks", response_model=list[WorkflowWebhookOut])
async def list_webhooks(
    workflow_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[WorkflowWebhook]:
    await _get_workflow_or_404(db, workflow_id)
    result = await db.execute(
        select(WorkflowWebhook)
        .where(WorkflowWebhook.workflow_id == workflow_id)
        .order_by(WorkflowWebhook.created_at.desc())
    )
    return list(result.scalars().all())


@router.patch("/{workflow_id}/webhooks/{webhook_id}", response_model=WorkflowWebhookOut)
async def update_webhook(
    workflow_id: str,
    webhook_id: str,
    body: WorkflowWebhookUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
) -> WorkflowWebhook:
    webhook = await _get_webhook_or_404(db, workflow_id, webhook_id)
    if body.channel_id is not None:
        if await db.get(Channel, body.channel_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
        webhook.channel_id = body.channel_id
    if body.name is not None:
        webhook.name = body.name
    if body.input_template is not None:
        webhook.input_template = body.input_template
    if body.enabled is not None:
        webhook.enabled = body.enabled
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.delete("/{workflow_id}/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    workflow_id: str, webhook_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> None:
    """#242: owner-gated -- see create_webhook's docstring."""
    webhook = await _get_webhook_or_404(db, workflow_id, webhook_id)
    await db.delete(webhook)
    await db.commit()


@router.post(
    "/{workflow_id}/webhooks/{webhook_id}/rotate-secret", response_model=WorkflowWebhookCreated
)
async def rotate_webhook_secret(
    workflow_id: str, webhook_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> WorkflowWebhookCreated:
    """Mints a fresh secret for an existing webhook (its id/URL stays the
    same) -- the recovery path if a secret leaks, without having to
    reconfigure the sender's URL. Same shown-once treatment as create.

    #242: owner-gated -- see create_webhook's docstring."""
    webhook = await _get_webhook_or_404(db, workflow_id, webhook_id)
    secret = keys.generate_webhook_secret()
    encryption_key = get_session_key_store().get_webhook_secret_key()
    nonce, ciphertext = encrypt_webhook_secret(secret, encryption_key)
    webhook.secret_nonce = nonce
    webhook.secret_ciphertext = ciphertext
    await db.commit()
    await db.refresh(webhook)
    return WorkflowWebhookCreated(
        **WorkflowWebhookOut.model_validate(webhook).model_dump(), secret=secret
    )


@router.get("/runs/failed", response_model=list[FailedWorkflowRunOut])
async def list_failed_runs(
    db: DbSession, _: CurrentWorkspaceId, limit: int = Query(default=50, ge=1, le=200)
) -> list[FailedWorkflowRunOut]:
    """#94: a failed WorkflowRun today is only discoverable by scrolling to
    the right rivulet at the right time or opening that one workflow's own
    run history (list_runs, below) — there's no cross-workflow view of
    "what's failing right now". This is that view: every failed run across
    every workflow, newest first, joined against Workflow for a display
    name and Rivulet for the channel a human would jump into to see what
    happened. Deliberately just a read — no acknowledgment/resolution
    state, no auto-remediation (#94's later layers); those need their own
    design pass and aren't blocked on this landing first."""
    result = await db.execute(
        select(WorkflowRun, Workflow.name, Rivulet.channel_id)
        .join(Workflow, WorkflowRun.workflow_id == Workflow.id)
        .join(Rivulet, WorkflowRun.rivulet_id == Rivulet.id)
        .where(WorkflowRun.status == "failed")
        .order_by(WorkflowRun.started_at.desc())
        .limit(limit)
    )
    return [
        FailedWorkflowRunOut(
            **WorkflowRunOut.model_validate(run).model_dump(),
            workflow_name=workflow_name,
            channel_id=channel_id,
        )
        for run, workflow_name, channel_id in result.all()
    ]


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunOut])
async def list_runs(workflow_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[WorkflowRun]:
    """Most recent 50 runs, newest first — mirrors api/agents.py's
    get_agent_runs (FR-3.5)."""
    await _get_workflow_or_404(db, workflow_id)
    result = await db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.started_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())


@router.get("/{workflow_id}/runs/{run_id}/node-runs", response_model=list[WorkflowNodeRunOut])
async def list_node_runs(
    workflow_id: str, run_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[WorkflowNodeRun]:
    run = await db.get(WorkflowRun, run_id)
    if run is None or run.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    result = await db.execute(
        select(WorkflowNodeRun)
        .where(WorkflowNodeRun.workflow_run_id == run_id)
        .order_by(WorkflowNodeRun.started_at)
    )
    return list(result.scalars().all())
