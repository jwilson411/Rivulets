"""Workspace-level usage/cost dashboard (#28): aggregates AgentRun rows
(dispatch/service.py's `_record_agent_run`) across every agent, broken down
by agent and by model, over a selectable day/week/month window.

Same source table FR-3.5's per-agent run history (`GET /agents/{id}/runs`)
reads from — see db/models.py's AgentRun docstring for why a new table was
needed at all (AgentOS's own SqliteDb isn't queried anywhere in this app).

Cost is a best-effort static-pricing estimate (agentos/pricing.py); a run
whose model isn't in that table contributes tokens but no cost, which would
silently understate spend if not surfaced — `cost_incomplete` on the
response flags that at least one included run's cost couldn't be estimated.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import Agent, AgentRun

router = APIRouter(prefix="/usage", tags=["usage"])

UsageRange = Literal["day", "week", "month"]

_RANGE_DELTAS: dict[UsageRange, timedelta] = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}


class UsageByAgent(BaseModel):
    agent_id: str
    agent_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    run_count: int


class UsageByModel(BaseModel):
    model: str
    tier: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    run_count: int


class UsageOut(BaseModel):
    range: UsageRange
    since: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: float
    cost_incomplete: bool
    run_count: int
    by_agent: list[UsageByAgent]
    by_model: list[UsageByModel]


@dataclass
class _Bucket:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False  # at least one run in this bucket had a priced model
    cost_unknown: bool = False  # at least one run in this bucket had an unpriced model
    run_count: int = 0
    agent_name: str | None = None  # only populated on by_agent buckets

    def add(self, run: AgentRun) -> None:
        self.input_tokens += run.input_tokens
        self.output_tokens += run.output_tokens
        self.total_tokens += run.total_tokens
        self.run_count += 1
        if run.cost_usd is not None:
            self.cost_usd += run.cost_usd
            self.cost_known = True
        else:
            self.cost_unknown = True


@router.get("", response_model=UsageOut)
async def get_usage(db: DbSession, _: CurrentWorkspaceId, range: UsageRange = "week") -> UsageOut:
    since = datetime.now(UTC) - _RANGE_DELTAS[range]
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    result = await db.execute(
        select(AgentRun, Agent.name)
        .join(Agent, Agent.id == AgentRun.agent_id)
        .where(AgentRun.created_at >= since_iso)
    )
    rows = result.all()

    totals = _Bucket()
    by_agent: dict[str, _Bucket] = {}
    by_model: dict[tuple[str, str | None], _Bucket] = {}

    for run, agent_name in rows:
        totals.add(run)

        agent_bucket = by_agent.setdefault(run.agent_id, _Bucket(agent_name=agent_name))
        agent_bucket.add(run)

        model_bucket = by_model.setdefault((run.model, run.tier), _Bucket())
        model_bucket.add(run)

    return UsageOut(
        range=range,
        since=since_iso,
        total_input_tokens=totals.input_tokens,
        total_output_tokens=totals.output_tokens,
        total_tokens=totals.total_tokens,
        total_cost_usd=round(totals.cost_usd, 6),
        cost_incomplete=totals.cost_unknown,
        run_count=totals.run_count,
        by_agent=sorted(
            (
                UsageByAgent(
                    agent_id=agent_id,
                    agent_name=bucket.agent_name or "",
                    input_tokens=bucket.input_tokens,
                    output_tokens=bucket.output_tokens,
                    total_tokens=bucket.total_tokens,
                    cost_usd=round(bucket.cost_usd, 6) if bucket.cost_known else None,
                    run_count=bucket.run_count,
                )
                for agent_id, bucket in by_agent.items()
            ),
            key=lambda a: a.total_tokens,
            reverse=True,
        ),
        by_model=sorted(
            (
                UsageByModel(
                    model=model,
                    tier=tier,
                    input_tokens=bucket.input_tokens,
                    output_tokens=bucket.output_tokens,
                    total_tokens=bucket.total_tokens,
                    cost_usd=round(bucket.cost_usd, 6) if bucket.cost_known else None,
                    run_count=bucket.run_count,
                )
                for (model, tier), bucket in by_model.items()
            ),
            key=lambda m: m.total_tokens,
            reverse=True,
        ),
    )
