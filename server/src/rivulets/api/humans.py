"""Human identity directory (#14).

Read-only from here — Humans are created via POST /auth/identity (claiming
a new display name mints the row as a side effect of that claim), not a
dedicated create endpoint. This router just lists the directory so a
browser session's IdentityPicker can offer "continue as an existing
person" before claiming one.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import Human

router = APIRouter(prefix="/humans", tags=["humans"])


class HumanOut(BaseModel):
    id: str
    display_name: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[HumanOut])
async def list_humans(db: DbSession, _: CurrentWorkspaceId) -> list[Human]:
    """Workspace-gated only (not CurrentHumanId) — this must be reachable
    before an identity has been claimed."""
    result = await db.execute(select(Human).order_by(Human.display_name))
    return list(result.scalars().all())
