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
"""

import json
import logging

from croniter import CroniterError
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import (
    Agent,
    Channel,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowSchedule,
)
from rivulets.sync.publish import publish_current_state
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


class WorkflowOut(BaseModel):
    id: str
    name: str
    description: str | None
    published: bool
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


class WorkflowNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    agent_id: str | None = None
    child_workflow_id: str | None = None
    config: dict[str, object] | None = None
    retry_max_attempts: int | None = Field(default=None, ge=0, le=10)
    retry_backoff_seconds: int | None = Field(default=None, ge=0, le=3600)


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
        )


class WorkflowConnectionCreate(BaseModel):
    from_node_id: str | None = None  # None = this workflow's entry point
    to_node_id: str
    # None = always follow this edge. Otherwise exactly one of
    # {"contains": "text"} / {"not_contains": "text"} — see
    # workflows/engine.py's module docstring.
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
    cron_expression: str
    input_content: str
    enabled: bool
    next_fire_at: str
    last_fired_at: str | None
    consecutive_failures: int
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class CronPreviewRequest(BaseModel):
    cron_expression: str = Field(min_length=1, max_length=128)


class CronPreviewOut(BaseModel):
    valid: bool
    next_fire_at: str | None
    error: str | None


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


async def _get_schedule_or_404(
    db: DbSession, workflow_id: str, schedule_id: str
) -> WorkflowSchedule:
    schedule = await db.get(WorkflowSchedule, schedule_id)
    if schedule is None or schedule.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow schedule not found")
    return schedule


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
    await db.commit()
    await db.refresh(workflow)
    await publish_current_state(db, "workflow", workflow.id)
    return workflow


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    workflow = await _get_workflow_or_404(db, workflow_id)
    await db.delete(workflow)
    await db.commit()


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
    return [WorkflowNodeOut.from_row(row) for row in result.scalars().all()]


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
    await db.commit()
    await db.refresh(node)
    await publish_current_state(db, "workflow_node", node.id)
    return WorkflowNodeOut.from_row(node)


@router.delete("/{workflow_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(workflow_id: str, node_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    node = await _get_node_or_404(db, workflow_id, node_id)
    await db.delete(node)
    await db.commit()


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


@router.delete("/{workflow_id}/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    workflow_id: str, connection_id: str, db: DbSession, _: CurrentWorkspaceId
) -> None:
    connection = await db.get(WorkflowConnection, connection_id)
    if connection is None or connection.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow connection not found")
    await db.delete(connection)
    await db.commit()


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
        # again clean" signal.
        if body.enabled and not schedule.enabled:
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
