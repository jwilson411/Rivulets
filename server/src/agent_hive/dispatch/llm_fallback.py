"""LLM-based routing fallback (ADR-005 stage 2).

dispatch/engine.py's two-stage dispatcher only matches @mentions and
deterministic rules (keyword/regex/semantic patterns generated at
agent-creation time via rule_generation.py). A message that misses every
rule falls through to here: a single LLM call, given the message and the
team's agent names + descriptions, picks zero or more agents that should
respond.

Model selection reuses rule_generation.py's OQ-2 policy (workspace
override, falling back to the default/first configured provider's cheap
model) via the now-shared pick_dispatcher_model — both features answer
the same "which cheap model handles dispatcher-adjacent LLM calls"
question, so duplicating the policy would just be a second place for it
to drift out of sync.

Graceful degradation (NFR-2.4) mirrors rule_generation.py: no provider
configured, or the call fails for any reason, resolves to an empty list
(DispatchEngine.dispatch then reports DispatchMethod.NONE) rather than
raising — a broken/misconfigured LLM fallback must never take down
dispatch, since it's the last stage checked and by definition everything
before it already failed to match.

Also honors the `dispatcher.fallback_enabled` workspace setting
(api/settings.py's _DEFAULTS — declared there but never actually read
anywhere until now) so a workspace can turn this stage off entirely, e.g.
for cost control or to keep routing fully deterministic/auditable.

build_llm_fallback(db) closes over the request's own AsyncSession and
returns a plain callable matching dispatch.engine.LlmFallback's shape,
so dispatch/service.py can hand it straight to DispatchEngine(...) without
DispatchEngine needing any DB awareness of its own (ADR-001).
"""

import json
import logging

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.agentos.models import resolve_model
from agent_hive.db.models import WorkspaceSetting
from agent_hive.dispatch.engine import AgentDispatchInfo, LlmFallback
from agent_hive.dispatch.rule_generation import pick_dispatcher_model

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """You route a single chat message to the agent(s) on a team best suited
to respond, given each agent's name and description. This message already missed every
agent's own keyword/regex/semantic routing rules, so only route it if an agent's stated
purpose is still a genuine match for what's being asked — most unmatched messages should
route to nobody rather than being forced onto the closest-sounding agent. Prefer routing
to zero agents over guessing. Only pick more than one agent if the message genuinely
needs more than one to respond (e.g. it asks two distinct, unrelated questions)."""


class _DispatchDecision(BaseModel):
    agent_names: list[str] = Field(
        default_factory=list,
        description="Exact names of the agent(s) that should respond, or empty if none fit.",
    )


def _build_prompt(message: str, agents: list[AgentDispatchInfo]) -> str:
    roster = "\n".join(f"- {a.name}: {a.description}" for a in agents)
    return f"Team agents:\n{roster}\n\nMessage: {message}"


async def _run_decision(model: Model, prompt: str) -> _DispatchDecision | None:
    """Isolated seam for tests to monkeypatch — everything above this is
    pure string/DB logic, everything below is the actual LLM call."""
    decider = AgnoAgent(model=model, instructions=_INSTRUCTIONS)
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # same benign gap noted in agentos/service.py's run_agent().
    run_output = await decider.arun(  # pyright: ignore[reportUnknownMemberType]
        prompt, output_schema=_DispatchDecision, stream=False
    )
    content = run_output.content
    return content if isinstance(content, _DispatchDecision) else None


async def _fallback_enabled(db: AsyncSession) -> bool:
    row = await db.get(WorkspaceSetting, "dispatcher.fallback_enabled")
    if row is None:
        return True  # api/settings.py's _DEFAULTS: on unless a row overrides it
    return bool(json.loads(row.value))


def build_llm_fallback(db: AsyncSession) -> LlmFallback:
    async def llm_fallback(message: str, agents: list[AgentDispatchInfo]) -> list[str]:
        if not await _fallback_enabled(db):
            return []

        provider_model = await pick_dispatcher_model(db)
        if provider_model is None:
            return []

        try:
            model = await resolve_model(db, provider_model)
            decision = await _run_decision(model, _build_prompt(message, agents))
        except Exception:
            logger.warning("LLM dispatch fallback failed", exc_info=True)
            return []

        if decision is None:
            return []
        name_to_id = {a.name.lower(): a.agent_id for a in agents}
        return [
            name_to_id[name.lower()] for name in decision.agent_names if name.lower() in name_to_id
        ]

    return llm_fallback
