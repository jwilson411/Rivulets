"""Dispatcher hit-rate tracking (#31, R-4 "Dispatcher LLM Cost Overruns").

Aggregates DispatchDecision rows (dispatch/service.py's `_dispatch_and_respond`,
one per DispatchEngine.dispatch() call) over a selectable window, mirroring
api/usage.py's day/week/month range pattern.

R-4's two mitigations, both served by this endpoint:
  - a dispatcher hit-rate metric (goal >80% resolved without the LLM
    fallback), surfaced in the Settings screen (#25).
  - a UI warning once the rolling fallback rate crosses 50% — `fallback_warning`
    here, computed over whatever range the UI asks for.

"Hit" and "fallback" are defined by `llm_invoked`, not by `method` alone:
`method == "none"` covers both "fallback wasn't configured" and "fallback
ran but matched nobody" (engine.py's DispatchResult docstring), and only the
latter costs money — `llm_invoked` is what disambiguates them.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import DispatchDecision

router = APIRouter(prefix="/dispatch", tags=["dispatch"])

DispatchRange = Literal["day", "week", "month"]

_RANGE_DELTAS: dict[DispatchRange, timedelta] = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}


class MethodCount(BaseModel):
    method: str
    count: int


class HitRateOut(BaseModel):
    range: DispatchRange
    since: str
    total_decisions: int
    hit_count: int  # llm_invoked is False
    fallback_count: int  # llm_invoked is True
    hit_rate: float | None  # None when total_decisions == 0
    fallback_rate: float | None
    fallback_warning: bool  # fallback_rate is not None and > 0.5
    by_method: list[MethodCount]


@router.get("/hit-rate", response_model=HitRateOut)
async def get_hit_rate(
    db: DbSession, _: CurrentWorkspaceId, range: DispatchRange = "week"
) -> HitRateOut:
    since = datetime.now(UTC) - _RANGE_DELTAS[range]
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    result = await db.execute(
        select(DispatchDecision).where(DispatchDecision.created_at >= since_iso)
    )
    decisions = result.scalars().all()

    by_method: dict[str, int] = {}
    hit_count = 0
    fallback_count = 0
    for decision in decisions:
        by_method[decision.method] = by_method.get(decision.method, 0) + 1
        if decision.llm_invoked:
            fallback_count += 1
        else:
            hit_count += 1

    total = hit_count + fallback_count
    hit_rate = hit_count / total if total else None
    fallback_rate = fallback_count / total if total else None

    return HitRateOut(
        range=range,
        since=since_iso,
        total_decisions=total,
        hit_count=hit_count,
        fallback_count=fallback_count,
        hit_rate=hit_rate,
        fallback_rate=fallback_rate,
        fallback_warning=fallback_rate is not None and fallback_rate > 0.5,
        by_method=sorted(
            (MethodCount(method=method, count=count) for method, count in by_method.items()),
            key=lambda m: m.count,
            reverse=True,
        ),
    )
