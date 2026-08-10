"""#96: tracing.py's helpers (start_trace/start_span/finish_span/
finish_trace/prune_old_traces) exercised directly against a DB session --
the schema-linking behavior that dispatch/service.py and workflows/
engine.py's instrumentation both build on."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import RunSpan, RunTrace
from rivulets.tracing import (
    TraceContext,
    finish_span,
    finish_trace,
    prune_old_traces,
    start_span,
    start_trace,
)


async def test_start_span_is_noop_without_trace_ctx(db_session: AsyncSession) -> None:
    span_id = await start_span(db_session, None, span_type="agent_run", entity_id="x", name="n")
    assert span_id is None
    assert (await db_session.scalars(select(RunSpan))).first() is None


async def test_finish_span_is_noop_without_span_id(db_session: AsyncSession) -> None:
    # Should not raise -- every instrumented call site relies on this.
    await finish_span(db_session, None, status="completed")


async def test_finish_trace_is_noop_without_trace_id(db_session: AsyncSession) -> None:
    await finish_trace(db_session, None)


async def test_start_trace_creates_root_context(db_session: AsyncSession) -> None:
    ctx = await start_trace(
        db_session,
        trigger_type="message",
        label="hello there",
        rivulet_id=None,
        channel_id=None,
    )
    assert ctx.parent_span_id is None
    trace = await db_session.get(RunTrace, ctx.trace_id)
    assert trace is not None
    assert trace.label == "hello there"
    assert trace.status == "running"


async def test_start_trace_truncates_and_collapses_whitespace(db_session: AsyncSession) -> None:
    ctx = await start_trace(
        db_session,
        trigger_type="message",
        label="a\nb   " + ("c" * 200),
        rivulet_id=None,
        channel_id=None,
    )
    trace = await db_session.get(RunTrace, ctx.trace_id)
    assert trace is not None
    assert "\n" not in trace.label
    assert len(trace.label) <= 80
    assert trace.label.endswith("…")


async def test_span_nests_under_parent(db_session: AsyncSession) -> None:
    ctx = await start_trace(
        db_session, trigger_type="message", label="root", rivulet_id=None, channel_id=None
    )
    parent_id = await start_span(
        db_session, ctx, span_type="dispatch_decision", entity_id="d1", name="dispatch"
    )
    child_ctx = TraceContext(ctx.trace_id, parent_id)
    child_id = await start_span(
        db_session, child_ctx, span_type="agent_run", entity_id=None, name="Agent"
    )
    child = await db_session.get(RunSpan, child_id)
    assert child is not None
    assert child.parent_span_id == parent_id
    assert child.trace_id == ctx.trace_id


async def test_finish_span_sets_status_and_backfills_entity_id(db_session: AsyncSession) -> None:
    ctx = await start_trace(
        db_session, trigger_type="message", label="root", rivulet_id=None, channel_id=None
    )
    span_id = await start_span(
        db_session, ctx, span_type="agent_run", entity_id=None, name="Agent"
    )
    await finish_span(
        db_session,
        span_id,
        status="completed",
        entity_id="agent-run-123",
        model="anthropic:claude-haiku-4-5-20251001",
        cost_usd=0.01,
        total_tokens=50,
    )
    span = await db_session.get(RunSpan, span_id)
    assert span is not None
    assert span.status == "completed"
    assert span.entity_id == "agent-run-123"
    assert span.model == "anthropic:claude-haiku-4-5-20251001"
    assert span.cost_usd == 0.01
    assert span.total_tokens == 50
    assert span.completed_at is not None
    assert span.duration_ms is not None
    assert span.duration_ms >= 0


async def test_finish_trace_aggregates_spans_and_marks_completed(db_session: AsyncSession) -> None:
    ctx = await start_trace(
        db_session, trigger_type="message", label="root", rivulet_id=None, channel_id=None
    )
    span_a = await start_span(db_session, ctx, span_type="agent_run", entity_id=None, name="A")
    await finish_span(db_session, span_a, status="completed", cost_usd=0.5, total_tokens=100)
    span_b = await start_span(db_session, ctx, span_type="agent_run", entity_id=None, name="B")
    await finish_span(db_session, span_b, status="completed", cost_usd=0.25, total_tokens=40)

    await finish_trace(db_session, ctx.trace_id)

    trace = await db_session.get(RunTrace, ctx.trace_id)
    assert trace is not None
    assert trace.status == "completed"
    assert trace.span_count == 2
    assert trace.total_cost_usd == 0.75
    assert trace.total_tokens == 140
    assert trace.completed_at is not None


async def test_finish_trace_marks_error_if_any_span_errored(db_session: AsyncSession) -> None:
    ctx = await start_trace(
        db_session, trigger_type="message", label="root", rivulet_id=None, channel_id=None
    )
    ok_span = await start_span(db_session, ctx, span_type="agent_run", entity_id=None, name="A")
    await finish_span(db_session, ok_span, status="completed")
    bad_span = await start_span(db_session, ctx, span_type="agent_run", entity_id=None, name="B")
    await finish_span(db_session, bad_span, status="error")

    await finish_trace(db_session, ctx.trace_id)

    trace = await db_session.get(RunTrace, ctx.trace_id)
    assert trace is not None
    assert trace.status == "error"


async def test_prune_old_traces_deletes_only_stale_ones(db_session: AsyncSession) -> None:
    old_trace = RunTrace(
        trigger_type="message",
        label="old",
        started_at=(datetime.now(UTC) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    fresh_trace = RunTrace(trigger_type="message", label="fresh")
    db_session.add_all([old_trace, fresh_trace])
    await db_session.flush()
    old_span = RunSpan(trace_id=old_trace.id, span_type="agent_run", name="x")
    db_session.add(old_span)
    await db_session.commit()

    deleted = await prune_old_traces(db_session, retention_days=30)
    await db_session.commit()

    assert deleted == 1
    remaining_ids = {t.id for t in (await db_session.scalars(select(RunTrace))).all()}
    assert remaining_ids == {fresh_trace.id}
    # Cascade: the old trace's span goes with it.
    assert (await db_session.scalars(select(RunSpan))).first() is None
