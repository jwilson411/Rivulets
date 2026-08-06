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
"""

import logging

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import ModelTier, resolve_model, resolve_tier_model

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """You classify how complex a chat message is to answer well, so it can
be routed to a cheap/fast model or a slower/capable one. Respond `capable` only when the
message genuinely requires deep reasoning, multi-step problem solving, nuanced judgment,
or long/complex output. Respond `cheap` for everything else -- short factual questions,
simple lookups, routine formatting, casual conversation -- since most messages are simple
and defaulting to `capable` would be needlessly expensive."""


class _ComplexityDecision(BaseModel):
    tier: ModelTier


async def _run_classification(model: Model, message: str) -> _ComplexityDecision | None:
    """Isolated seam for tests to monkeypatch — everything above this is
    pure DB/string logic, everything below is the actual LLM call."""
    classifier = AgnoAgent(model=model, instructions=_INSTRUCTIONS)
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # same benign gap noted in agentos/service.py's run_agent().
    run_output = await classifier.arun(  # pyright: ignore[reportUnknownMemberType]
        message, output_schema=_ComplexityDecision, stream=False
    )
    content = run_output.content
    return content if isinstance(content, _ComplexityDecision) else None


async def classify_tier(db: AsyncSession, message: str) -> ModelTier:
    try:
        cheap_provider_model = await resolve_tier_model(db, "cheap")
        if cheap_provider_model is None:
            return "cheap"
        model = await resolve_model(db, cheap_provider_model)
        decision = await _run_classification(model, message)
    except Exception:
        logger.warning("Complexity classification failed", exc_info=True)
        return "cheap"

    if decision is None:
        return "cheap"
    return decision.tier
