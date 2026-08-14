"""Spend budget caps (#97): a $ limit per day/week/month at agent, team, or
workspace scope, with a configurable action on breach — alert-only (post a
system_alert, non-blocking) or hard_stop (refuse new agent invocations in
that scope until the window resets or a human overrides it).

Cap *definitions* (db/models.py's BudgetCap) are synced workspace config,
same as WorkspaceSetting. Enforcement here is local-only per peer, computed
from this node's own AgentRun history — no cross-peer spend aggregation
(that needs a singleton consumer of #101's coordinator election, which
is not wired yet — the election primitive itself shipped). See BudgetCap's
docstring for the full reasoning; this is the same local-only precedent
WorkflowSchedule (#92) already set for itself.

Mirrors dispatch/guards.py's shape: state lives in `budget_cap_state`, one
row per cap, not synced (each node tracks its own alert/override bookkeeping
against its own locally-computed spend).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import Agent, AgentRun, BudgetCap, BudgetCapState, TeamAgent


def _period_start(period: str, now: datetime) -> str:
    """Calendar-aligned window boundary in UTC, formatted like utcnow_iso.
    'day' = midnight UTC; 'week' = Monday 00:00 UTC (ISO week); 'month' =
    first of the month, 00:00 UTC. Deliberately calendar-aligned, not a
    rolling look-back like usage.py's _RANGE_DELTAS -- a hard-stop cap
    needs an actual reset boundary, not a decaying window that never
    clears."""
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        start = midnight
    elif period == "week":
        start = midnight - timedelta(days=midnight.weekday())
    elif period == "month":
        start = midnight.replace(day=1)
    else:
        raise ValueError(f"unknown budget period: {period!r}")
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BudgetStatus:
    cap: BudgetCap
    period_start: str
    spend_usd: float
    # Runs in this window whose model isn't in pricing.py's static table
    # (cost_usd is None) -- excluded from spend_usd rather than treated as
    # zero cost, same as usage.py's cost_incomplete pattern. A hard_stop
    # cap treats unpriced_run_count > 0 as blocking on its own (see
    # check_budget_caps) even when `breached` below is False, since spend
    # nobody can see could already be over the limit.
    unpriced_run_count: int
    # Pure dollar-threshold comparison (spend_usd >= cap.limit_usd) --
    # doesn't factor in unpriced_run_count. check_budget_caps and
    # api/budgets.py's _status_out both add the unpriced check on top of
    # this for hard_stop caps specifically; 'alert' caps only ever look at
    # this field, unchanged from before.
    breached: bool


async def compute_spend(
    db: AsyncSession, cap: BudgetCap, now: datetime | None = None
) -> BudgetStatus:
    period_start = _period_start(cap.period, now or datetime.now(UTC))
    query = select(AgentRun).where(AgentRun.created_at >= period_start)
    if cap.scope_type == "agent":
        query = query.where(AgentRun.agent_id == cap.agent_id)
    elif cap.scope_type == "team":
        query = query.where(
            AgentRun.agent_id.in_(
                select(TeamAgent.agent_id).where(TeamAgent.team_id == cap.team_id)
            )
        )
    # 'workspace': no filter -- one Workspace row per node, nothing else in
    # this codebase filters queries by workspace id either.
    runs = (await db.execute(query)).scalars().all()

    spend_usd = 0.0
    unpriced_run_count = 0
    for run in runs:
        if run.cost_usd is None:
            unpriced_run_count += 1
        else:
            spend_usd += run.cost_usd

    return BudgetStatus(
        cap=cap,
        period_start=period_start,
        spend_usd=spend_usd,
        unpriced_run_count=unpriced_run_count,
        breached=spend_usd >= cap.limit_usd,
    )


@dataclass
class BudgetAlert:
    cap: BudgetCap
    status: BudgetStatus


async def _get_or_create_state(db: AsyncSession, cap_id: str) -> BudgetCapState:
    state = await db.get(BudgetCapState, cap_id)
    if state is None:
        state = BudgetCapState(cap_id=cap_id)
        db.add(state)
        await db.flush()
    return state


async def _applicable_caps(
    db: AsyncSession, agent: Agent | None, team_id: str | None
) -> list[BudgetCap]:
    """Most-specific scope first: agent, then team, then workspace --
    matches check_budget_caps' short-circuit order for hard_stop caps.

    `agent` is None for call sites with no single agent to attribute the
    spend to (knowledge_base ingest's workspace-only check below) -- same
    "can't be more specific than workspace" shape as AgentRun.agent_id's
    own null case, just skipping the agent-scope query entirely rather
    than querying for a cap that could never match."""
    caps: list[BudgetCap] = []
    if agent is not None:
        caps.extend(
            (
                await db.execute(
                    select(BudgetCap).where(
                        BudgetCap.enabled.is_(True),
                        BudgetCap.scope_type == "agent",
                        BudgetCap.agent_id == agent.id,
                    )
                )
            )
            .scalars()
            .all()
        )
    if team_id is not None:
        caps.extend(
            (
                await db.execute(
                    select(BudgetCap).where(
                        BudgetCap.enabled.is_(True),
                        BudgetCap.scope_type == "team",
                        BudgetCap.team_id == team_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    caps.extend(
        (
            await db.execute(
                select(BudgetCap).where(
                    BudgetCap.enabled.is_(True), BudgetCap.scope_type == "workspace"
                )
            )
        )
        .scalars()
        .all()
    )
    return caps


async def check_budget_caps(
    db: AsyncSession, agent: Agent | None, team_id: str | None
) -> tuple[list[BudgetAlert], BudgetAlert | None]:
    """Checks agent-scope, then team-scope (if team_id given), then
    workspace-scope caps, most specific first. Returns (alerts, blocking):
    `alerts` is every breached 'alert'-action cap not yet alerted this
    period (caller posts a system_alert for each, non-blocking); `blocking`
    is the first 'hard_stop' cap not currently overridden for this period
    whose dollar spend is breached *or* whose window contains an unpriced
    run (see BudgetStatus.unpriced_run_count's docstring -- a hard_stop cap
    can't certify spend is under its limit when some of that spend is
    unknown, so it refuses rather than silently failing open), or None.
    Short-circuits on the first hard_stop match -- the call is refused
    either way, so there's no need to keep checking once one is found."""
    alerts: list[BudgetAlert] = []
    blocking: BudgetAlert | None = None
    for cap in await _applicable_caps(db, agent, team_id):
        status = await compute_spend(db, cap)
        if cap.action == "hard_stop":
            if not (status.breached or status.unpriced_run_count > 0):
                continue
            state = await _get_or_create_state(db, cap.id)
            if state.override_active and state.override_period_start == status.period_start:
                continue  # overridden for this period
            blocking = BudgetAlert(cap=cap, status=status)
            break
        else:  # 'alert'
            if not status.breached:
                continue
            state = await _get_or_create_state(db, cap.id)
            if state.alerted_at is not None and state.period_start == status.period_start:
                continue  # already alerted this period, don't repeat every turn
            state.period_start = status.period_start
            state.alerted_at = utcnow_iso()
            alerts.append(BudgetAlert(cap=cap, status=status))
    return alerts, blocking


def budget_alert_text(alert: BudgetAlert, *, blocked: bool) -> str:
    cap = alert.cap
    status = alert.status
    verb = "has been blocked because it exceeds" if blocked else "has reached"
    return (
        f"Spend for this {cap.scope_type} budget cap {verb} its "
        f"${cap.limit_usd:.2f}/{cap.period} limit "
        f"(${status.spend_usd:.2f} spent this {cap.period})."
    )


class BudgetCapBlockedError(Exception):
    """Raised by `enforce_budget_caps` for the non-channel-dispatch call
    sites (workflow agent nodes, eval agent/workflow runs, KB ingest) --
    they don't have dispatch/service.py's own message/SSE/pending-approval
    plumbing built around check_budget_caps' return value, they just need
    the call refused with a clear reason (#320)."""

    def __init__(self, alert: BudgetAlert):
        self.alert = alert
        super().__init__(budget_alert_text(alert, blocked=True))


async def enforce_budget_caps(
    db: AsyncSession, agent: Agent | None, team_id: str | None
) -> list[BudgetAlert]:
    """check_budget_caps, raising BudgetCapBlockedError in place of a
    separate 'blocking' return value -- for callers that just need the
    invocation refused, not dispatch/service.py's richer handling (a
    posted system_alert Message, an SSE event, a PendingApproval row).
    Returns the breached 'alert'-action caps, if any, for the caller to
    surface however fits its own context (or ignore, same as before this
    helper existed for these call sites)."""
    alerts, blocking = await check_budget_caps(db, agent, team_id)
    if blocking is not None:
        raise BudgetCapBlockedError(blocking)
    return alerts
