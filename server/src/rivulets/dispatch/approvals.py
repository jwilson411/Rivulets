"""Unified approval queue (#102): a generic PendingApproval row plus the
approve/reject logic for the three sources that create one --
schedule_workflow_trigger's agent-created WorkflowSchedule (#93),
check_budget_caps' tripped hard_stop BudgetCap (#97), and
agentos/tool_audit.py's unattended sensitive-tool gate (#100).

Each source previously had its own bespoke "how does a human unblock
this" action (flip WorkflowSchedule.enabled, POST /budgets/{id}/override,
flip Agent.approved_for_unattended_tools). This module doesn't replace
any of those three actions -- `approve_pending_approval` performs exactly
the same write each already did, just reached through one shared
entity/endpoint instead of three. See PendingApproval's own docstring
(db/models.py) for why the row itself is local-only, not synced.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import Agent, BudgetCap, BudgetCapState, PendingApproval, WorkflowSchedule
from rivulets.dispatch.budgets import compute_spend
from rivulets.sync.publish import publish_current_state


class PendingApprovalError(Exception):
    """Raised when approving/rejecting a row whose source no longer
    exists (e.g. the schedule was deleted, the budget cap was removed) or
    that's already been resolved."""


_SOURCE_COLUMNS: dict[str, str] = {
    "schedule": "schedule_id",
    "budget": "budget_cap_id",
    "tool_guardrail": "agent_id",
}


async def create_or_get_pending_approval(
    db: AsyncSession,
    source_type: str,
    *,
    schedule_id: str | None = None,
    budget_cap_id: str | None = None,
    agent_id: str | None = None,
    title: str,
    detail: str,
) -> PendingApproval:
    """Returns the existing open (`status='pending'`) row for this exact
    source if one exists, rather than creating a duplicate -- same dedup
    reasoning as dispatch/budgets.py's `alerted_at` check: a schedule
    stuck disabled, a budget still tripped, or an agent still unapproved
    would otherwise grow a new row on every single trigger (every poll
    tick, every blocked invocation, every unattended run attempt) instead
    of staying one open item a human can act on once."""
    column_name = _SOURCE_COLUMNS[source_type]
    values_by_column = {
        "schedule_id": schedule_id,
        "budget_cap_id": budget_cap_id,
        "agent_id": agent_id,
    }
    column_value = values_by_column[column_name]
    column = getattr(PendingApproval, column_name)
    existing = (
        await db.execute(
            select(PendingApproval).where(
                PendingApproval.source_type == source_type,
                PendingApproval.status == "pending",
                column == column_value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    approval = PendingApproval(
        source_type=source_type,
        schedule_id=schedule_id,
        budget_cap_id=budget_cap_id,
        agent_id=agent_id,
        title=title,
        detail=detail,
    )
    db.add(approval)
    await db.flush()
    return approval


async def _approve_schedule(db: AsyncSession, approval: PendingApproval) -> None:
    from rivulets.workflows.scheduler import compute_next_fire_at

    schedule = await db.get(WorkflowSchedule, approval.schedule_id)
    if schedule is None:
        raise PendingApprovalError("The schedule this approval was for no longer exists.")
    # Mirrors api/workflows.py's update_schedule re-enable branch: recompute
    # next_fire_at from *now*, don't fire off whatever stale slot the
    # schedule was created with while it sat pending.
    if schedule.cron_expression is not None:
        schedule.next_fire_at = compute_next_fire_at(schedule.cron_expression)
    schedule.consecutive_failures = 0
    schedule.enabled = True
    schedule.updated_at = utcnow_iso()


async def _approve_budget(db: AsyncSession, approval: PendingApproval, human_id: str) -> None:
    cap = await db.get(BudgetCap, approval.budget_cap_id)
    if cap is None:
        raise PendingApprovalError("The budget cap this approval was for no longer exists.")
    result = await compute_spend(db, cap)
    state = await db.get(BudgetCapState, cap.id)
    if state is None:
        state = BudgetCapState(cap_id=cap.id)
        db.add(state)
    state.override_active = True
    state.override_period_start = result.period_start
    state.override_by = human_id
    state.override_at = utcnow_iso()


async def _approve_tool_guardrail(db: AsyncSession, approval: PendingApproval) -> None:
    agent = await db.get(Agent, approval.agent_id)
    if agent is None:
        raise PendingApprovalError("The agent this approval was for no longer exists.")
    agent.approved_for_unattended_tools = True
    agent.updated_at = utcnow_iso()
    agent.vector_clock += 1
    await db.flush()
    await publish_current_state(db, "agent", agent.id)


async def approve_pending_approval(
    db: AsyncSession, approval: PendingApproval, human_id: str
) -> PendingApproval:
    if approval.status != "pending":
        raise PendingApprovalError(f"This approval was already {approval.status}.")
    if approval.source_type == "schedule":
        await _approve_schedule(db, approval)
    elif approval.source_type == "budget":
        await _approve_budget(db, approval, human_id)
    elif approval.source_type == "tool_guardrail":
        await _approve_tool_guardrail(db, approval)
    approval.status = "approved"
    approval.resolved_by = human_id
    approval.resolved_at = utcnow_iso()
    await db.commit()
    await db.refresh(approval)
    return approval


async def reject_pending_approval(
    db: AsyncSession, approval: PendingApproval, human_id: str
) -> PendingApproval:
    """No source-specific side effect -- rejecting just closes the item.
    Each source is already left in its safe/blocked state by definition
    (a schedule stays disabled, a budget stays tripped, an agent stays
    unapproved), so there's nothing further to undo."""
    if approval.status != "pending":
        raise PendingApprovalError(f"This approval was already {approval.status}.")
    approval.status = "rejected"
    approval.resolved_by = human_id
    approval.resolved_at = utcnow_iso()
    await db.commit()
    await db.refresh(approval)
    return approval
