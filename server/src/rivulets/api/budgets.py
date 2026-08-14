"""Budget cap CRUD, status, and override (#97) -- see BudgetCap's docstring
(db/models.py) and dispatch/budgets.py's module docstring for the full
design: cap *definitions* sync across the workspace like Team/Agent;
enforcement (dispatch/budgets.py's check_budget_caps, wired into
dispatch/service.py's _invoke_agent) is local-only per peer.

Mutating routes are owner-gated (OwnerGrant), same bucket as provider
credentials/backups/sync config per api/deps.py's require_owner_grant
docstring -- a spend cap is workspace policy, not something an invited
human should be able to loosen or remove. Reads (list/status) are open to
any grant, matching usage.py's/dispatch.py's existing openness.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from rivulets.api.deps import CurrentHumanId, CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.base import utcnow_iso
from rivulets.db.models import Agent, BudgetCap, BudgetCapState, Team
from rivulets.dispatch.budgets import compute_spend
from rivulets.sync.publish import publish_current_state, publish_tombstone

router = APIRouter(prefix="/budgets", tags=["budgets"])

BudgetScope = Literal["agent", "team", "workspace"]
BudgetPeriod = Literal["day", "week", "month"]
BudgetAction = Literal["alert", "hard_stop"]


class BudgetCapCreate(BaseModel):
    scope_type: BudgetScope
    agent_id: str | None = None
    team_id: str | None = None
    period: BudgetPeriod
    limit_usd: float
    action: BudgetAction

    @model_validator(mode="after")
    def _scope_matches_ids(self) -> "BudgetCapCreate":
        if self.scope_type == "agent" and (self.agent_id is None or self.team_id is not None):
            raise ValueError("scope_type='agent' requires agent_id and no team_id")
        if self.scope_type == "team" and (self.team_id is None or self.agent_id is not None):
            raise ValueError("scope_type='team' requires team_id and no agent_id")
        if self.scope_type == "workspace" and (
            self.agent_id is not None or self.team_id is not None
        ):
            raise ValueError("scope_type='workspace' allows neither agent_id nor team_id")
        if self.limit_usd <= 0:
            raise ValueError("limit_usd must be > 0")
        return self


class BudgetCapUpdate(BaseModel):
    # scope_type/agent_id/team_id are not patchable -- re-parenting a cap
    # would silently change what spend it's judged against; delete and
    # recreate instead, same precedent EvalSuiteUpdate already set for its
    # own agent_id/workflow_id.
    period: BudgetPeriod | None = None
    limit_usd: float | None = None
    action: BudgetAction | None = None
    enabled: bool | None = None


class BudgetCapOut(BaseModel):
    id: str
    scope_type: str
    agent_id: str | None
    team_id: str | None
    period: str
    limit_usd: float
    action: str
    enabled: bool

    model_config = {"from_attributes": True}


class BudgetStatusOut(BudgetCapOut):
    period_start: str
    spend_usd: float
    unpriced_run_count: int
    breached: bool
    # action == 'hard_stop' AND (breached OR unpriced_run_count > 0) AND not
    # currently overridden -- see dispatch/budgets.py's check_budget_caps
    # for why an unpriced run blocks on its own.
    blocked: bool
    override_active: bool


async def _get_or_404(db: DbSession, cap_id: str) -> BudgetCap:
    cap = await db.get(BudgetCap, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Budget cap not found")
    return cap


async def _status_out(db: DbSession, cap: BudgetCap) -> BudgetStatusOut:
    result = await compute_spend(db, cap)
    state = await db.get(BudgetCapState, cap.id)
    override_active = bool(
        state is not None
        and state.override_active
        and state.override_period_start == result.period_start
    )
    blocked = (
        cap.action == "hard_stop"
        and (result.breached or result.unpriced_run_count > 0)
        and not override_active
    )
    return BudgetStatusOut(
        id=cap.id,
        scope_type=cap.scope_type,
        agent_id=cap.agent_id,
        team_id=cap.team_id,
        period=cap.period,
        limit_usd=cap.limit_usd,
        action=cap.action,
        enabled=cap.enabled,
        period_start=result.period_start,
        spend_usd=result.spend_usd,
        unpriced_run_count=result.unpriced_run_count,
        breached=result.breached,
        blocked=blocked,
        override_active=override_active,
    )


@router.get("", response_model=list[BudgetStatusOut])
async def list_budget_caps(db: DbSession, _: CurrentWorkspaceId) -> list[BudgetStatusOut]:
    caps = (await db.execute(select(BudgetCap))).scalars().all()
    return [await _status_out(db, cap) for cap in caps]


@router.post("", response_model=BudgetCapOut, status_code=status.HTTP_201_CREATED)
async def create_budget_cap(
    body: BudgetCapCreate, db: DbSession, _: OwnerGrant, __: CurrentWorkspaceId
) -> BudgetCap:
    if body.agent_id is not None and await db.get(Agent, body.agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if body.team_id is not None and await db.get(Team, body.team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")

    cap = BudgetCap(
        scope_type=body.scope_type,
        agent_id=body.agent_id,
        team_id=body.team_id,
        period=body.period,
        limit_usd=body.limit_usd,
        action=body.action,
    )
    db.add(cap)
    await db.commit()
    await db.refresh(cap)
    await publish_current_state(db, "budget_cap", cap.id)
    return cap


@router.patch("/{cap_id}", response_model=BudgetCapOut)
async def update_budget_cap(
    cap_id: str, body: BudgetCapUpdate, db: DbSession, _: OwnerGrant, __: CurrentWorkspaceId
) -> BudgetCap:
    cap = await _get_or_404(db, cap_id)
    if body.period is not None:
        cap.period = body.period
    if body.limit_usd is not None:
        if body.limit_usd <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "limit_usd must be > 0")
        cap.limit_usd = body.limit_usd
    if body.action is not None:
        cap.action = body.action
    if body.enabled is not None:
        cap.enabled = body.enabled
    cap.updated_at = utcnow_iso()
    await db.commit()
    await publish_current_state(db, "budget_cap", cap.id)
    return cap


@router.delete("/{cap_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget_cap(
    cap_id: str, db: DbSession, _: OwnerGrant, __: CurrentWorkspaceId
) -> None:
    cap = await _get_or_404(db, cap_id)
    await db.delete(cap)
    await db.commit()
    await publish_tombstone(db, "budget_cap", cap_id)


@router.get("/{cap_id}/status", response_model=BudgetStatusOut)
async def get_budget_cap_status(
    cap_id: str, db: DbSession, _: CurrentWorkspaceId
) -> BudgetStatusOut:
    cap = await _get_or_404(db, cap_id)
    return await _status_out(db, cap)


@router.post("/{cap_id}/override", response_model=BudgetStatusOut)
async def override_budget_cap(
    cap_id: str, db: DbSession, _: OwnerGrant, human_id: CurrentHumanId
) -> BudgetStatusOut:
    """Clears a tripped hard_stop cap for the remainder of its current
    period only -- not a permanent override, which would defeat the cap's
    purpose. Auto-expires the moment the period rolls over
    (BudgetCapState.override_period_start must match the freshly computed
    period_start to count -- see its docstring), so there's nothing to
    re-trip or clean up next period."""
    cap = await _get_or_404(db, cap_id)
    result = await compute_spend(db, cap)

    state = await db.get(BudgetCapState, cap_id)
    if state is None:
        state = BudgetCapState(cap_id=cap_id)
        db.add(state)
    state.override_active = True
    state.override_period_start = result.period_start
    state.override_by = human_id
    state.override_at = utcnow_iso()
    await db.commit()
    return await _status_out(db, cap)
