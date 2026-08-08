"""Cron-based automatic workflow triggering (#92): run_scheduler_loop is
the app's first plain-asyncio recurring background task (app.py's
lifespan starts/cancels it) -- unlike sync/engine.py's trio-on-a-separate-
thread loop, which exists only because py-libp2p needs it, this has no
such constraint and stays a bare `while True` with its own DB session per
tick (db/session.py's session_scope, the same pattern non-request-scoped
DB work elsewhere already uses).

Missed fires are skipped, never backfilled (#92's explicit decision):
compute_next_fire_at always seeds croniter from wall-clock `now`, never
from the previous (possibly long-stale) next_fire_at -- see
WorkflowSchedule's own docstring (db/models.py) for why that's what makes
"skip, don't catch up" fall out automatically rather than being a special
case here.
"""

import asyncio
import logging
from datetime import UTC, datetime

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import Channel, Message, Rivulet, Workflow, WorkflowSchedule
from rivulets.db.session import session_scope
from rivulets.sync.publish import publish_current_state
from rivulets.workflows.engine import run_workflow

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 5


def compute_next_fire_at(cron_expression: str, base: datetime | None = None) -> str:
    """Raises croniter.CroniterError (or a subclass) on an unparseable
    expression -- callers (api/workflows.py's create/update/preview, and
    _fire below) decide how to surface that. `base` always defaults to
    wall-clock now, never a stored next_fire_at, so a schedule that's been
    stale for days doesn't produce a rapid-fire catch-up burst -- see
    this module's docstring."""
    base = base or datetime.now(UTC)
    next_dt = croniter(cron_expression, base).get_next(datetime)
    return next_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_scheduler_loop() -> None:
    """Runs for the app's lifetime (app.py's lifespan). One poll tick's
    exception never kills the loop -- it's logged and the loop keeps
    running, so one broken schedule can't silently stop every other
    schedule in the workspace from ever firing again."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("Workflow scheduler tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _tick() -> None:
    now = utcnow_iso()
    async with session_scope() as db:
        due = (
            await db.scalars(
                select(WorkflowSchedule).where(
                    WorkflowSchedule.enabled.is_(True),
                    WorkflowSchedule.next_fire_at <= now,
                )
            )
        ).all()
        for schedule in due:
            await _fire(db, schedule)


async def _fire(db: AsyncSession, schedule: WorkflowSchedule) -> None:
    """Fires one due schedule. Advances next_fire_at unconditionally and
    first, before anything that could fail -- a workflow that's
    unpublished/deleted, a missing channel, or a run that errors out must
    still not re-trip this schedule on every single poll tick forever."""
    schedule.next_fire_at = compute_next_fire_at(schedule.cron_expression)

    # Same gate every other trigger path shares (workflows/trigger.py's
    # find_workflow_by_name) -- replicated by id here since a schedule
    # already has workflow_id, not workflow.name.
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == schedule.workflow_id, Workflow.published)
    )
    if workflow is None:
        schedule.consecutive_failures += 1
        logger.warning(
            "Schedule %s's workflow is unpublished or missing; skipping this fire", schedule.id
        )
    else:
        try:
            await _fire_published_workflow(db, schedule, workflow)
            schedule.consecutive_failures = 0
        except Exception:
            logger.exception("Schedule %s failed to fire", schedule.id)
            schedule.consecutive_failures += 1

    if schedule.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        schedule.enabled = False
        logger.warning(
            "Disabling schedule %s after %d consecutive failures",
            schedule.id,
            schedule.consecutive_failures,
        )
    await db.commit()


async def _fire_published_workflow(
    db: AsyncSession, schedule: WorkflowSchedule, workflow: Workflow
) -> None:
    channel = await db.get(Channel, schedule.channel_id)
    if channel is None:
        raise RuntimeError(f"Schedule {schedule.id}'s target channel no longer exists")

    rivulet = Rivulet(channel_id=schedule.channel_id, created_by=schedule.id)
    db.add(rivulet)
    await db.flush()
    kickoff = Message(
        rivulet_id=rivulet.id,
        sender_type="system",
        sender_name="system",
        content=f"Scheduled trigger: /{workflow.name} {schedule.input_content}".rstrip(),
        content_type="system_alert",
    )
    db.add(kickoff)
    await db.commit()
    await db.refresh(rivulet)
    await publish_current_state(db, "rivulet", rivulet.id)
    await publish_current_state(db, "message", kickoff.id)

    schedule.last_fired_at = utcnow_iso()
    run = await run_workflow(
        db,
        workflow,
        rivulet,
        schedule.input_content,
        triggered_by="schedule",
        triggered_by_id=schedule.id,
    )
    if run.status == "failed":
        raise RuntimeError(f"Scheduled run {run.id} failed: {run.error_message}")
