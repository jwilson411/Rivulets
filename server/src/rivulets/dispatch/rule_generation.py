"""Generates deterministic routing rules for a new/edited agent via a
cheap LLM call (FR-3.3, US-017), so an agent has working rules from the
moment it's created instead of only responding to @mentions.

Dispatcher model selection follows OQ-2: a workspace-level override,
falling back to the workspace's default provider (or its first configured
one), mapped to that provider's designated cheap model. If no provider is
configured at all — or the LLM call fails for any reason — this returns
an empty rule list rather than raising (NFR-2.4): the agent still gets
created, it just responds to @mentions only until rules are set manually
or generation is retried (e.g. by editing the agent, which regenerates
per FR-3.4).
"""

import json
import logging
from typing import Literal

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import resolve_default_provider, resolve_model
from rivulets.db.models import WorkspaceSetting
from rivulets.dispatch.rules import is_valid_regex

logger = logging.getLogger(__name__)

# Per OQ-2's stated defaults (Anthropic -> Haiku, OpenAI -> GPT-4o-mini),
# extended to deepseek since it's one of FR-1.4's minimum providers too.
_DEFAULT_DISPATCHER_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}

_INSTRUCTIONS = """You generate deterministic message-routing rules for a chat-based
multi-agent system. Given an agent's name, description, and instructions, output 1-5
rules that would reliably match messages this agent should handle.

Favor keyword rules for concrete domain terms the agent's users would actually type.
Use semantic rules for paraphrased trigger phrases that don't share exact wording with
the keywords. Use regex sparingly, only for structured patterns like ticket numbers or
file extensions. Do not be overly broad — only match messages genuinely relevant to
this agent's stated purpose, not everything vaguely nearby."""

RuleType = Literal["keyword", "regex", "semantic"]


class GeneratedRule(BaseModel):
    rule_type: RuleType
    keywords: list[str] = Field(
        default_factory=list,
        description="Required for keyword/semantic rules: distinctive words or short phrases.",
    )
    regex: str = Field(default="", description="Required for regex rules: a Python regex pattern.")
    priority: int = Field(default=5, ge=0, le=20)


class GeneratedRoutingRules(BaseModel):
    rules: list[GeneratedRule] = Field(
        description=(
            "1-5 rules for this agent. Do not generate 'always' or 'mention_only' "
            "rules — those are set manually, not inferred."
        )
    )


StoredRule = tuple[str, str, int]  # (rule_type, pattern, priority) — AgentRoutingRule shape


def _to_stored_rule(generated: GeneratedRule) -> StoredRule | None:
    """Returns None for a regex rule the model produced that doesn't
    actually compile (#366) -- same re.compile check service.py's
    _parse_routing_rule already applies to the update_agent_routing_rules
    tool path, applied here so a landmine pattern never reaches the DB in
    the first place and bricks every future dispatch on this agent."""
    if generated.rule_type == "regex":
        if not is_valid_regex(generated.regex):
            return None
        return "regex", generated.regex, generated.priority
    return generated.rule_type, json.dumps(generated.keywords), generated.priority


async def pick_dispatcher_model(db: AsyncSession) -> str | None:
    override_row = await db.get(WorkspaceSetting, "dispatcher.model_override")
    if override_row is not None:
        override = json.loads(override_row.value)
        if override:
            return str(override)

    chosen = await resolve_default_provider(db)
    if chosen is None:
        return None
    model_name = _DEFAULT_DISPATCHER_MODELS.get(chosen.provider)
    if model_name is None:
        return None
    return f"{chosen.provider}:{model_name}"


async def _run_generator(model: Model, prompt: str) -> GeneratedRoutingRules | None:
    """Isolated seam for tests to monkeypatch — everything above this is
    pure DB/string logic, everything below is the actual LLM call."""
    generator = AgnoAgent(model=model, instructions=_INSTRUCTIONS)
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # same benign gap noted in agentos/service.py's run_agent().
    run_output = await generator.arun(  # pyright: ignore[reportUnknownMemberType]
        prompt, output_schema=GeneratedRoutingRules, stream=False
    )
    content = run_output.content
    return content if isinstance(content, GeneratedRoutingRules) else None


_ASSISTANT_ALWAYS: StoredRule = ("always", "", 0)


async def generate_routing_rules(
    db: AsyncSession, name: str, description: str, instructions: str
) -> list[StoredRule]:
    # #406: the starter generalist (and any later agent named Assistant)
    # answers everyday channel chat. Inferring keywords like "specialist,
    # expert, coder" from its description made "How are you all doing
    # today?" a silent dispatch-none, which the composer then dressed up
    # as a successful delivery to the team.
    if name.lower() == "assistant":
        return [_ASSISTANT_ALWAYS]

    provider_model = await pick_dispatcher_model(db)
    if provider_model is None:
        return []

    prompt = f"Agent name: {name}\nDescription: {description}\nInstructions: {instructions}"
    try:
        model = await resolve_model(db, provider_model)
        generated = await _run_generator(model, prompt)
    except Exception:
        logger.warning("Routing rule generation failed for agent %r", name, exc_info=True)
        return []

    if generated is None:
        return []
    stored: list[StoredRule] = []
    for r in generated.rules:
        rule = _to_stored_rule(r)
        if rule is None:
            logger.warning("Dropping invalid generated regex rule for agent %r: %r", name, r.regex)
            continue
        stored.append(rule)
    return stored
