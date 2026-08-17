"""Unified run tracing (#96): links AgentRun/DispatchDecision/WorkflowRun/
WorkflowNodeRun rows -- previously only correlatable by `created_at`
proximity -- into a parent/child span tree under one RunTrace per root
trigger (a human message or a scheduled workflow fire). See db/models.py's
RunTrace/RunSpan docstrings for the schema and what's deliberately out of
scope (cross-peer traces, tracing across a workflow pause/resume boundary).

Every helper below is a no-op (or returns None) when given a None
TraceContext/span id. Tracing is opt-in, threaded explicitly from the two
root entry points -- api/rivulets.py's create_rivulet/post_message,
workflows/scheduler.py's _fire_published_workflow -- down through whatever
they call, rather than auto-starting at every call site that happens to
reach dispatch_and_respond/run_workflow. That keeps paths this feature
doesn't cover yet -- remote P2P agent dispatch (dispatch/service.py's
invoke_agent_remotely), eval suite runs (evals/runner.py) -- silently
untraced instead of requiring an explicit opt-out at each of those sites.

Retention: `prune_old_traces`/`run_retention_loop` mirror workflows/
scheduler.py's run_scheduler_loop pattern (its own plain-asyncio background
task, started/cancelled in app.py's lifespan) -- traces accumulate
continuously, unlike backup.py's startup-only snapshot retention, so a
recurring poll loop is the closer precedent.

#414: a trace can be left `status='running'` forever when the process
dies after `start_trace` committed (a 500, a container restart) and
never reaches `finish_trace`. `reap_stale_traces` fails those leftovers
-- zero-span traces after a short timeout, and (on process start) every
other running trace that isn't a workflow pause waiting on a person.
`close_trace` is the same close used by POST /runs/{id}/cancel.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import RunSpan, RunTrace, WorkflowRun
from rivulets.db.session import session_scope

logger = logging.getLogger(__name__)

_LABEL_MAX_LEN = 80
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_ERROR_STATUSES = {"error", "failed"}
_TERMINAL_STATUSES = frozenset({"completed", "error", "cancelled"})

RETENTION_POLL_INTERVAL_SECONDS = 3600
DEFAULT_RETENTION_DAYS = 30
# A first span is written as soon as dispatch/a workflow starts real work.
# Longer than this with no spans means start_trace committed and the
# process never got further -- not a live run (#414).
STALE_ZERO_SPAN_MINUTES = 5


@dataclass(frozen=True)
class TraceContext:
    """Carried explicitly through every traced call chain (dispatch/
    service.py, workflows/engine.py) rather than a contextvar -- consistent
    with this codebase's existing preference for explicit, threaded state
    (workflows/engine.py's own `_RunContext`/`ancestry`) over ambient
    globals. `parent_span_id` is the span whatever's about to happen next
    should nest under; None only at a trace's own root."""

    trace_id: str
    parent_span_id: str | None


def _truncate_label(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _LABEL_MAX_LEN:
        return collapsed
    return collapsed[: _LABEL_MAX_LEN - 1] + "…"


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, _ISO_FORMAT).replace(tzinfo=UTC)


def _duration_ms(started_at: str, completed_at: str) -> int:
    delta = _parse_iso(completed_at) - _parse_iso(started_at)
    return max(0, int(delta.total_seconds() * 1000))


async def start_trace(
    db: AsyncSession,
    *,
    trigger_type: str,
    label: str,
    rivulet_id: str | None,
    channel_id: str | None,
) -> TraceContext:
    """Opens a new root trace. Called only at a genuine trigger -- never at
    an internal recursion point, which instead continues the caller's own
    TraceContext, nesting a child span under whatever span caused it."""
    trace = RunTrace(
        trigger_type=trigger_type,
        label=_truncate_label(label),
        rivulet_id=rivulet_id,
        channel_id=channel_id,
    )
    db.add(trace)
    await db.flush()
    return TraceContext(trace_id=trace.id, parent_span_id=None)


async def start_span(
    db: AsyncSession,
    ctx: TraceContext | None,
    *,
    span_type: str,
    entity_id: str | None,
    name: str,
) -> str | None:
    """Records the start of one step under `ctx`'s trace, nested under its
    current parent_span_id. Returns None (writing nothing) when `ctx` is
    None -- the untraced-call-site default every instrumented site relies
    on to stay a no-op without its own `if traced:` branch."""
    if ctx is None:
        return None
    span = RunSpan(
        trace_id=ctx.trace_id,
        parent_span_id=ctx.parent_span_id,
        span_type=span_type,
        entity_id=entity_id,
        name=name,
    )
    db.add(span)
    # span_count is otherwise only written at finish_trace, so a still-
    # running trace listed in the UI would always say "0 steps" even after
    # work had started. Increment here so the list stays honest mid-run.
    trace = await db.get(RunTrace, ctx.trace_id)
    if trace is not None:
        trace.span_count += 1
    await db.flush()
    return span.id


async def finish_span(
    db: AsyncSession,
    span_id: str | None,
    *,
    status: str,
    entity_id: str | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    total_tokens: int | None = None,
) -> None:
    """No-op when `span_id` is None -- either an untraced call site, or (for
    a workflow_run span) a run that paused: its span deliberately stays
    'running' until `resume_workflow` re-attaches to it and finishes it
    itself (see RunTrace's docstring).

    `entity_id` back-fills the row `start_span` couldn't yet point at --
    an agent_run span opens before the AgentRun row it describes exists
    (created only once the run actually finishes, by
    agentos/accounting.py's record_agent_run), so its id is only known
    here. Every other span_type already knows its entity_id at start_span
    time and leaves this None (a no-op column write, not a clear)."""
    if span_id is None:
        return
    span = await db.get(RunSpan, span_id)
    if span is None:
        return
    span.status = status
    if entity_id is not None:
        span.entity_id = entity_id
    span.model = model
    span.cost_usd = cost_usd
    span.total_tokens = total_tokens
    completed_at = utcnow_iso()
    span.completed_at = completed_at
    span.duration_ms = _duration_ms(span.started_at, completed_at)


async def _aggregate_trace(db: AsyncSession, trace: RunTrace) -> tuple[int, bool]:
    """Writes span_count/cost/tokens from this trace's RunSpan rows.
    Returns (span_count, any_span_errored)."""
    stats = (
        await db.execute(
            select(
                func.count(RunSpan.id),
                func.sum(RunSpan.cost_usd),
                func.sum(RunSpan.total_tokens),
            ).where(RunSpan.trace_id == trace.id)
        )
    ).one()
    span_count, total_cost_usd, total_tokens = stats
    has_error = await db.scalar(
        select(func.count(RunSpan.id)).where(
            RunSpan.trace_id == trace.id, RunSpan.status.in_(_ERROR_STATUSES)
        )
    )
    trace.span_count = span_count or 0
    trace.total_cost_usd = float(total_cost_usd) if total_cost_usd is not None else None
    trace.total_tokens = int(total_tokens or 0)
    return trace.span_count, bool(has_error)


async def finish_trace(db: AsyncSession, trace_id: str | None) -> None:
    """No-op when `trace_id` is None. Aggregates this trace's own RunSpan
    rows into its denormalized span_count/total_cost_usd/total_tokens, and
    marks it 'error' if any span ended in error/failed, else 'completed' --
    called once by whichever root entry point opened the trace, after its
    top-level dispatch_and_respond/run_workflow call returns. A trace whose
    root workflow_run paused never reaches this call at all (the root
    entry point's own call already returned once the pause happened) and
    is deliberately left 'running' -- see RunTrace's docstring.

    Also a no-op when the trace is already terminal: a user cancel or the
    #414 reaper may have closed it while the original request was still
    in flight, and that close wins rather than being overwritten back to
    completed."""
    if trace_id is None:
        return
    trace = await db.get(RunTrace, trace_id)
    if trace is None or trace.status in _TERMINAL_STATUSES:
        return

    _, has_error = await _aggregate_trace(db, trace)
    trace.status = "error" if has_error else "completed"
    trace.completed_at = utcnow_iso()


async def close_trace(db: AsyncSession, trace_id: str, *, status: str) -> RunTrace | None:
    """Force-close a still-running trace (user cancel, or the #414 reaper).
    Open spans are finished with the same status so the detail view doesn't
    leave steps 'running' under a closed parent. No-op (returns the row)
    when the trace is already terminal; None if it doesn't exist."""
    trace = await db.get(RunTrace, trace_id)
    if trace is None:
        return None
    if trace.status in _TERMINAL_STATUSES:
        return trace
    open_spans = (
        await db.scalars(
            select(RunSpan).where(RunSpan.trace_id == trace_id, RunSpan.completed_at.is_(None))
        )
    ).all()
    for span in open_spans:
        await finish_span(db, span.id, status=status)
    await _aggregate_trace(db, trace)
    trace.status = status
    trace.completed_at = utcnow_iso()
    return trace


async def _paused_workflow_trace_ids(db: AsyncSession) -> set[str]:
    """Traces whose workflow_run span points at a WorkflowRun still waiting
    on a person -- the one 'running' leftover that is not a crash (#414 /
    RunTrace's pause/resume gap)."""
    rows = await db.scalars(
        select(RunSpan.trace_id)
        .join(WorkflowRun, WorkflowRun.id == RunSpan.entity_id)
        .where(RunSpan.span_type == "workflow_run", WorkflowRun.status == "awaiting_human")
    )
    return set(rows.all())


async def reap_stale_traces(db: AsyncSession, *, include_orphaned: bool = False) -> int:
    """Fail traces left 'running' with no work happening (#414).

    Always: a running trace with no RunSpan rows older than
    STALE_ZERO_SPAN_MINUTES -- start_trace committed, then the request
    died before any step.

    `include_orphaned=True` (process start): also fail every other
    running trace that isn't a workflow pause waiting on a person. The
    previous process is gone, so nothing is still executing them.

    Returns the number of traces closed.
    """
    paused_ids = await _paused_workflow_trace_ids(db)
    to_close: list[str] = []

    cutoff = (datetime.now(UTC) - timedelta(minutes=STALE_ZERO_SPAN_MINUTES)).strftime(_ISO_FORMAT)
    has_span = select(RunSpan.id).where(RunSpan.trace_id == RunTrace.id).exists()
    zero_span_ids = (
        await db.scalars(
            select(RunTrace.id).where(
                RunTrace.status == "running",
                RunTrace.started_at < cutoff,
                ~has_span,
            )
        )
    ).all()
    to_close.extend(tid for tid in zero_span_ids if tid not in paused_ids)

    if include_orphaned:
        orphan_ids = (
            await db.scalars(select(RunTrace.id).where(RunTrace.status == "running"))
        ).all()
        already = set(to_close)
        to_close.extend(tid for tid in orphan_ids if tid not in paused_ids and tid not in already)

    for tid in to_close:
        await close_trace(db, tid, status="error")
    return len(to_close)


async def prune_old_traces(db: AsyncSession, *, retention_days: int) -> int:
    """Deletes every RunTrace started more than `retention_days` ago
    (cascading to its RunSpan rows) regardless of status -- a trace stuck
    'running' past this age (the pause/resume gap RunTrace's docstring
    describes) is itself stale enough to be worth pruning rather than kept
    alive forever waiting for a reply that may never come. Returns the
    number of traces deleted."""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime(_ISO_FORMAT)
    # Select ids first rather than reading .rowcount off the delete result --
    # CursorResult.rowcount isn't part of AsyncSession.execute()'s statically
    # known Result[Any] return type, and this composes just as cheaply into
    # one extra indexed query (idx_run_trace_started).
    stale_ids = (await db.scalars(select(RunTrace.id).where(RunTrace.started_at < cutoff))).all()
    if not stale_ids:
        return 0
    await db.execute(delete(RunTrace).where(RunTrace.id.in_(stale_ids)))
    return len(stale_ids)


async def run_retention_loop() -> None:
    """Runs for the app's lifetime (app.py's lifespan), mirroring workflows/
    scheduler.py's run_scheduler_loop exactly: a bare `while True` with its
    own DB session per tick, one tick's exception logged and swallowed so
    it never kills the loop."""
    while True:
        try:
            await _retention_tick()
        except Exception:
            logger.exception("Run trace retention tick failed")
        await asyncio.sleep(RETENTION_POLL_INTERVAL_SECONDS)


async def _retention_tick() -> None:
    """One retention poll: its own `session_scope()`, matching workflows/
    scheduler.py's `_tick()` -- pulled out of `run_retention_loop` so tests
    can drive a single tick directly instead of the infinite loop.

    Also reaps zero-span leftovers (#414) so a 500 that never restarts
    the process still gets cleaned without waiting for someone to open
    the Runs page (list_runs reaps lazily too)."""
    async with session_scope() as db:
        reaped = await reap_stale_traces(db)
        deleted = await prune_old_traces(db, retention_days=DEFAULT_RETENTION_DAYS)
        await db.commit()
        if reaped:
            logger.info("Reaped %d stale running trace(s)", reaped)
        if deleted:
            logger.info(
                "Pruned %d run trace(s) older than %d days", deleted, DEFAULT_RETENTION_DAYS
            )
