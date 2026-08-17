"""Generates deterministic routing rules for a new/edited agent via a
cheap LLM call (FR-3.3, US-017), so an agent has working rules from the
moment it's created instead of only responding to @mentions.

Dispatcher model selection follows OQ-2: a workspace-level override,
falling back to the workspace's default provider (or its first configured
one), mapped to that provider's designated cheap model. If no provider is
configured at all — or the LLM call fails for any reason — this returns
an empty rule list rather than raising (NFR-2.4), except for the named
starter specialists, which fall back to a few real keywords (#410). The
agent still gets created either way.
"""

import json
import logging
from typing import Literal, cast

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import resolve_default_provider, resolve_model
from rivulets.db.models import WorkspaceSetting
from rivulets.dispatch.rules import is_overly_broad_regex, is_valid_regex

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

Favor keyword rules: 3-8 distinctive words or short phrases a user would actually type
for this agent's job. Do not use generic role words (specialist, expert, assistant,
agent, coder) unless they are this agent's real domain.

Use regex only for a specific structured token (ticket id, order number, file
extension). Never emit a regex that would match ordinary chat, random tokens, or
"any word plus a number". Prefer a keyword like "https://" over a home-grown URL
regex. Do not be overly broad — only match messages genuinely relevant to this
agent's stated purpose."""

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

# #410: starter specialists get a few real keywords instead of an LLM
# catch-all. Mention still works regardless. Assistant is `always` above.
_STARTER_KEYWORDS: dict[str, list[str]] = {
    "coder": ["code", "debug", "implement", "refactor", "stack trace"],
    "researcher": ["research", "look up", "sources", "https://", "http://"],
    "writer": ["draft", "rewrite", "proofread", "copyedit", "prose"],
}

_GENERIC_KEYWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "it",
        "this",
        "that",
        "agent",
        "assistant",
        "specialist",
        "expert",
    }
)


def starter_keyword_rule(name: str) -> StoredRule | None:
    keywords = _STARTER_KEYWORDS.get(name.lower())
    if not keywords:
        return None
    return "keyword", json.dumps(keywords), 10


def _useful_keywords(keywords: list[str]) -> list[str]:
    useful: list[str] = []
    seen: set[str] = set()
    for raw in keywords:
        text = raw.strip()
        if len(text) < 2:
            continue
        key = text.lower()
        if key in _GENERIC_KEYWORDS or key in seen:
            continue
        seen.add(key)
        useful.append(text)
    return useful


def _to_stored_rule(generated: GeneratedRule) -> StoredRule | None:
    """Returns None for a regex that doesn't compile (#366) or that would
    match ordinary chat (#410), and for keyword/semantic lists that
    collapse to nothing useful. Semantic is stored as keyword — matching
    is the same substring check today, and the agent sheet can show it."""
    if generated.rule_type == "regex":
        if not is_valid_regex(generated.regex):
            return None
        if is_overly_broad_regex(generated.regex):
            return None
        return "regex", generated.regex, generated.priority
    keywords = _useful_keywords(generated.keywords)
    if not keywords:
        return None
    return "keyword", json.dumps(keywords), generated.priority


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

    curated = starter_keyword_rule(name)

    provider_model = await pick_dispatcher_model(db)
    if provider_model is None:
        return [curated] if curated is not None else []

    prompt = f"Agent name: {name}\nDescription: {description}\nInstructions: {instructions}"
    try:
        model = await resolve_model(db, provider_model)
        generated = await _run_generator(model, prompt)
    except Exception:
        logger.warning("Routing rule generation failed for agent %r", name, exc_info=True)
        return [curated] if curated is not None else []

    if generated is None:
        return [curated] if curated is not None else []
    stored: list[StoredRule] = []
    for r in generated.rules:
        rule = _to_stored_rule(r)
        if rule is None:
            logger.warning(
                "Dropping unusable generated %s rule for agent %r: %r",
                r.rule_type,
                name,
                r.regex if r.rule_type == "regex" else r.keywords,
            )
            continue
        stored.append(rule)
    if not stored:
        return [curated] if curated is not None else []
    return _collapse_keyword_rules(stored)


def _collapse_keyword_rules(rules: list[StoredRule]) -> list[StoredRule]:
    """One keyword row the sheet can show in full, plus any surviving
    regex. Multiple keyword/semantic rows used to hide behind rule[0]."""
    merged: list[str] = []
    others: list[StoredRule] = []
    keyword_priority: int | None = None
    for rule_type, pattern, priority in rules:
        if rule_type != "keyword":
            others.append((rule_type, pattern, priority))
            continue
        keyword_priority = priority if keyword_priority is None else max(keyword_priority, priority)
        try:
            parsed = json.loads(pattern)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            for item in cast(list[object], parsed):
                if isinstance(item, str):
                    merged.append(item)
    useful = _useful_keywords(merged)
    collapsed: list[StoredRule] = []
    if useful:
        collapsed.append(("keyword", json.dumps(useful), keyword_priority or 10))
    collapsed.extend(others)
    return collapsed
