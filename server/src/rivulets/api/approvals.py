"""Unified approval queue (#102) -- list/approve/reject for PendingApproval
rows created by dispatch/service.py (schedule/budget sources) and
agentos/tool_audit.py (tool_guardrail source). See PendingApproval's
docstring (db/models.py) and dispatch/approvals.py's module docstring for
the full design.

Reads are open to any grant, matching budgets.py's/dispatch.py's existing
openness. Approve/reject are owner-gated: each one performs the same
workspace-policy-changing write its pre-#102 bespoke action already did
(re-enabling a schedule, overriding a budget cap, approving an agent for
unattended tool use) -- all three of those were already OwnerGrant-only
before this queue existed, so this doesn't loosen anything.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentHumanId, CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.models import PendingApproval
from rivulets.dispatch.approvals import (
    PendingApprovalError,
    approve_pending_approval,
    reject_pending_approval,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


class PendingApprovalOut(BaseModel):
    id: str
    source_type: str
    schedule_id: str | None
    budget_cap_id: str | None
    agent_id: str | None
    title: str
    detail: str
    status: str
    resolved_by: str | None
    resolved_at: str | None
    created_at: str

    model_config = {"from_attributes": True}


async def _get_or_404(db: DbSession, approval_id: str) -> PendingApproval:
    approval = await db.get(PendingApproval, approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    return approval


@router.get("", response_model=list[PendingApprovalOut])
async def list_pending_approvals(
    db: DbSession,
    _: CurrentWorkspaceId,
    status_filter: str | None = None,
) -> list[PendingApproval]:
    query = select(PendingApproval).order_by(PendingApproval.created_at.desc())
    if status_filter is not None:
        query = query.where(PendingApproval.status == status_filter)
    return list((await db.execute(query)).scalars().all())


@router.post("/{approval_id}/approve", response_model=PendingApprovalOut)
async def approve_approval(
    approval_id: str, db: DbSession, _: OwnerGrant, human_id: CurrentHumanId
) -> PendingApproval:
    approval = await _get_or_404(db, approval_id)
    try:
        return await approve_pending_approval(db, approval, human_id)
    except PendingApprovalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{approval_id}/reject", response_model=PendingApprovalOut)
async def reject_approval(
    approval_id: str, db: DbSession, _: OwnerGrant, human_id: CurrentHumanId
) -> PendingApproval:
    approval = await _get_or_404(db, approval_id)
    try:
        return await reject_pending_approval(db, approval, human_id)
    except PendingApprovalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
