"""Unified run tracing (#96): read-only views over the RunTrace/RunSpan
rows tracing.py records alongside AgentRun/DispatchDecision/WorkflowRun/
WorkflowNodeRun -- a workspace-wide "what's actually happening/happened"
list (`GET /runs`, mirroring api/workflows.py's `GET /workflows/runs/
failed` cross-entity shape) and a single trace's full span tree
(`GET /runs/{trace_id}`, mirroring `GET /workflows/{id}/runs/{run_id}/
node-runs`'s parent/child listing).

Local telemetry, same as the tables it links (RunTrace's own docstring) --
nothing here is created or mutated through this router; it's populated
entirely by tracing.py's start_trace/start_span/finish_span/finish_trace
calls threaded through dispatch/service.py and workflows/engine.py.

#100: an 'agent_run' span's `tool_calls` is populated from ToolCallLog
(agentos/tool_audit.py), joined on here rather than living in RunSpan
itself -- a span is one row per AgentRun regardless of how many tool
calls that run made, so folding tool-call detail directly into RunSpan
would mean either denormalizing an unbounded list into it or a second
per-span request; fetching every relevant ToolCallLog row alongside the
trace's spans in `get_run`'s single query (grouped client-side by
agent_run_id) keeps this endpoint's existing "whole trace detail in one
call" shape.
"""

from collections import defaultdict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import RunSpan, RunTrace, ToolCallLog

router = APIRouter(prefix="/runs", tags=["runs"])

_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200


class RunTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trigger_type: str
    label: str
    rivulet_id: str | None
    channel_id: str | None
    status: str
    span_count: int
    total_cost_usd: float | None
    total_tokens: int
    started_at: str
    completed_at: str | None


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    sensitive: bool
    status: str
    arguments_json: str | None
    result_summary: str | None
    duration_ms: int | None
    created_at: str


class RunSpanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_span_id: str | None
    span_type: str
    entity_id: str | None
    name: str
    status: str
    model: str | None
    cost_usd: float | None
    total_tokens: int | None
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    # Populated only for span_type == 'agent_run' (entity_id is that
    # run's AgentRun.id) -- empty for every other span type.
    tool_calls: list[ToolCallOut] = []


class RunTraceDetailOut(RunTraceOut):
    spans: list[RunSpanOut]


@router.get("", response_model=list[RunTraceOut])
async def list_runs(
    db: DbSession, _: CurrentWorkspaceId, limit: int = _DEFAULT_LIST_LIMIT
) -> list[RunTrace]:
    """Most recent traces, newest first."""
    result = await db.execute(
        select(RunTrace)
        .order_by(RunTrace.started_at.desc())
        .limit(max(1, min(limit, _MAX_LIST_LIMIT)))
    )
    return list(result.scalars().all())


@router.get("/{trace_id}", response_model=RunTraceDetailOut)
async def get_run(trace_id: str, db: DbSession, _: CurrentWorkspaceId) -> RunTraceDetailOut:
    trace = await db.get(RunTrace, trace_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    spans_result = await db.execute(
        select(RunSpan).where(RunSpan.trace_id == trace_id).order_by(RunSpan.started_at)
    )
    span_rows = list(spans_result.scalars().all())

    agent_run_ids = [s.entity_id for s in span_rows if s.span_type == "agent_run" and s.entity_id]
    tool_calls_by_run: dict[str, list[ToolCallOut]] = defaultdict(list)
    if agent_run_ids:
        calls_result = await db.execute(
            select(ToolCallLog)
            .where(ToolCallLog.agent_run_id.in_(agent_run_ids))
            .order_by(ToolCallLog.created_at)
        )
        for call in calls_result.scalars().all():
            assert call.agent_run_id is not None
            tool_calls_by_run[call.agent_run_id].append(ToolCallOut.model_validate(call))

    spans = [
        RunSpanOut(
            **RunSpanOut.model_validate(row).model_dump(exclude={"tool_calls"}),
            tool_calls=tool_calls_by_run.get(row.entity_id, []) if row.entity_id else [],
        )
        for row in span_rows
    ]
    return RunTraceDetailOut(**RunTraceOut.model_validate(trace).model_dump(), spans=spans)
