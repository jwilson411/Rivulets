"""#92: the workflow scheduler (workflows/scheduler.py) -- exercised
directly against a DB session (db_session fixture), mirroring
test_workflow_engine.py's engine-level (not HTTP) style. `_tick`/`_fire`
open their own session internally (session_scope()), which shares the
same in-memory SQLite DB as `db_session` (StaticPool, see
db/session.py's make_engine) as long as the fixture's own writes are
committed first.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import (
    Channel,
    Rivulet,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkflowRun,
    WorkflowSchedule,
)
from rivulets.workflows.scheduler import (
    MAX_CONSECUTIVE_FAILURES,
    _tick,  # pyright: ignore[reportPrivateUsage]
    compute_next_fire_at,
)


def test_compute_next_fire_at_seeds_from_now_not_from_stale_slot() -> None:
    """The key correctness test for #92's "skip missed fires, never
    backfill" decision: seeding from a base timestamp days in the past
    must not return a result anchored to that stale base."""
    stale_base = datetime.now(UTC) - timedelta(days=3)
    result = compute_next_fire_at("* * * * *", base=stale_base)
    result_dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert result_dt - datetime.now(UTC) < timedelta(minutes=2)


async def _make_channel(db: AsyncSession, name: str = "sched-channel") -> Channel:
    channel = Channel(name=name)
    db.add(channel)
    await db.flush()
    return channel


async def _make_published_workflow(db: AsyncSession, name: str = "digest") -> Workflow:
    workflow = Workflow(name=name, published=True)
    db.add(workflow)
    await db.flush()
    node = WorkflowNode(
        workflow_id=workflow.id,
        name="echo",
        node_type="transform",
        config_json=json.dumps({"template": "echo: {input}"}),
    )
    db.add(node)
    await db.flush()
    db.add(WorkflowConnection(workflow_id=workflow.id, from_node_id=None, to_node_id=node.id))
    await db.flush()
    return workflow


def _due_schedule(
    workflow_id: str, channel_id: str, cron_expression: str = "* * * * *"
) -> WorkflowSchedule:
    return WorkflowSchedule(
        workflow_id=workflow_id,
        channel_id=channel_id,
        cron_expression=cron_expression,
        input_content="hello",
        next_fire_at="2020-01-01T00:00:00Z",
    )


async def test_tick_fires_due_schedule_and_creates_rivulet_and_run(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    workflow = await _make_published_workflow(db_session)
    schedule = _due_schedule(workflow.id, channel.id)
    db_session.add(schedule)
    await db_session.commit()

    await _tick()

    await db_session.refresh(schedule)
    assert schedule.last_fired_at is not None
    assert schedule.consecutive_failures == 0
    assert schedule.next_fire_at > "2020-01-01T00:00:00Z"

    rivulet = await db_session.scalar(select(Rivulet).where(Rivulet.created_by == schedule.id))
    assert rivulet is not None
    assert rivulet.channel_id == channel.id

    run = await db_session.scalar(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id))
    assert run is not None
    assert run.triggered_by == "schedule"
    assert run.triggered_by_id == schedule.id
    assert run.status == "completed"


async def test_tick_skips_unpublished_workflow_and_increments_failures(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    workflow = Workflow(name="draft-only", published=False)
    db_session.add(workflow)
    await db_session.flush()
    schedule = _due_schedule(workflow.id, channel.id)
    db_session.add(schedule)
    await db_session.commit()

    await _tick()

    await db_session.refresh(schedule)
    assert schedule.consecutive_failures == 1
    rivulet_count = len((await db_session.scalars(select(Rivulet))).all())
    assert rivulet_count == 0
    run_count = len((await db_session.scalars(select(WorkflowRun))).all())
    assert run_count == 0


async def test_tick_ignores_disabled_schedule(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    workflow = await _make_published_workflow(db_session, name="disabled-flow")
    schedule = _due_schedule(workflow.id, channel.id)
    schedule.enabled = False
    db_session.add(schedule)
    await db_session.commit()
    original_next_fire_at = schedule.next_fire_at

    await _tick()

    await db_session.refresh(schedule)
    assert schedule.next_fire_at == original_next_fire_at
    assert schedule.last_fired_at is None


async def test_repeated_failures_auto_disable_schedule(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    workflow = Workflow(name="always-broken", published=False)
    db_session.add(workflow)
    await db_session.flush()
    schedule = _due_schedule(workflow.id, channel.id)
    db_session.add(schedule)
    await db_session.commit()

    for _ in range(MAX_CONSECUTIVE_FAILURES):
        schedule.next_fire_at = "2020-01-01T00:00:00Z"
        await db_session.commit()
        await _tick()
        await db_session.refresh(schedule)

    assert schedule.consecutive_failures == MAX_CONSECUTIVE_FAILURES
    assert schedule.enabled is False

    # A further tick must not attempt to fire it again.
    schedule.next_fire_at = "2020-01-01T00:00:00Z"
    await db_session.commit()
    failures_before = schedule.consecutive_failures
    await _tick()
    await db_session.refresh(schedule)
    assert schedule.consecutive_failures == failures_before


async def test_successful_fire_resets_consecutive_failures(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    workflow = await _make_published_workflow(db_session, name="recovers")
    schedule = _due_schedule(workflow.id, channel.id)
    schedule.consecutive_failures = 3
    db_session.add(schedule)
    await db_session.commit()

    await _tick()

    await db_session.refresh(schedule)
    assert schedule.consecutive_failures == 0
