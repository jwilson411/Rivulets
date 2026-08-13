"""Message-complexity classifier for Auto model mode (#23): picks the
`cheap` or `capable` tier for a single incoming message, so an "auto"
agent's model is chosen fresh per-message instead of fixed at creation.

Structurally mirrors llm_fallback.py: a Pydantic decision schema, an
isolated `_run_classification` seam for tests to monkeypatch, a single
non-streamed structured-output call. Deliberately always runs on the
*cheap* tier's own model — never pay capable-tier cost just to decide
which tier to use.

Degrades to `cheap` on any failure (no provider configured, the call
raises, malformed output) — the opposite of llm_fallback's degrade-to-`[]`.
Here "run on the cheap model" is the safe no-op; failing toward the
expensive tier when the safety mechanism itself breaks would be a
cost-runaway failure mode, not graceful degradation.

#246: a completed call is recorded via record_agent_run with
source='dispatcher_call', attributed to the agent this classification is
being run for (unlike llm_fallback.py's routing call, there's always a
single specific agent here) -- before this, the tokens/cost this stage
spent were invisible to the usage dashboard and every budget cap.
"""

import logging

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from agno.run.agent import RunOutput
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.models import ModelTier, resolve_model, resolve_tier_model
from rivulets.db.models import Agent

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """You classify how complex a chat message is to answer well, so it can
be routed to a cheap/fast model or a slower/capable one. Respond `capable` only when the
message genuinely requires deep reasoning, multi-step problem solving, nuanced judgment,
or long/complex output. Respond `cheap` for everything else -- short factual questions,
simple lookups, routine formatting, casual conversation -- since most messages are simple
and defaulting to `capable` would be needlessly expensive."""


class _ComplexityDecision(BaseModel):
    tier: ModelTier


async def _run_classification(model: Model, message: str) -> RunOutput:
    """Isolated seam for tests to monkeypatch — everything above this is
    pure DB/string logic, everything below is the actual LLM call. Returns
    the raw RunOutput (not just the parsed decision) so the caller can
    record its token/cost accounting (#246) as well as read `.content`."""
    classifier = AgnoAgent(model=model, instructions=_INSTRUCTIONS)
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # same benign gap noted in agentos/service.py's run_agent().
    return await classifier.arun(  # pyright: ignore[reportUnknownMemberType]
        message, output_schema=_ComplexityDecision, stream=False
    )


async def classify_tier(db: AsyncSession, agent: Agent, message: str) -> ModelTier:
    try:
        cheap_provider_model = await resolve_tier_model(db, "cheap")
        if cheap_provider_model is None:
            return "cheap"
        model = await resolve_model(db, cheap_provider_model)
        run_output = await _run_classification(model, message)
    except Exception:
        logger.warning("Complexity classification failed", exc_info=True)
        return "cheap"

    # #246: record this call's spend against the agent it's classifying
    # for -- unlike llm_fallback.py's routing call, there's always exactly
    # one agent here.
    await record_agent_run(
        db, agent, cheap_provider_model, None, "completed", run_output, source="dispatcher_call"
    )

    content = run_output.content
    decision = content if isinstance(content, _ComplexityDecision) else None
    if decision is None:
        return "cheap"
    return decision.tier
