"""Two-stage channel dispatcher (FR-4.1, ADR-005).

Stage 1 — deterministic rule matching over all agents on the channel's
team. Cheap, in-process, must stay under 50ms p95 for 50 agents (NFR-1.1).

Stage 2 — only when no agent's rules matched, an injected LLM fallback
callable decides. The callable is injected rather than called directly
here so this module has zero dependency on a specific LLM provider or on
AgentOS — per ADR-001, agent execution belongs to AgentOS, not to
hand-rolled logic in the App Server.

@mentions (FR-4.5) bypass both stages entirely.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from agent_hive.dispatch.rules import Rule, RuleType, rule_matches

LlmFallback = Callable[[str, list["AgentDispatchInfo"]], Awaitable[list[str]]]

_MENTION_RE = re.compile(r"@([A-Za-z0-9_-]+)")


@dataclass(frozen=True, slots=True)
class AgentDispatchInfo:
    agent_id: str
    name: str
    rules: list[Rule] = field(default_factory=list[Rule])


class DispatchMethod(StrEnum):
    MENTION = "mention"
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    agent_ids: list[str]
    method: DispatchMethod


class DispatchEngine:
    def __init__(self, llm_fallback: LlmFallback | None = None) -> None:
        self._llm_fallback = llm_fallback

    def match_mentions(self, message: str, agents: list[AgentDispatchInfo]) -> list[str]:
        mentioned_names = {m.group(1).lower() for m in _MENTION_RE.finditer(message)}
        if not mentioned_names:
            return []
        return [a.agent_id for a in agents if a.name.lower() in mentioned_names]

    def match_deterministic(self, message: str, agents: list[AgentDispatchInfo]) -> list[str]:
        matched: list[str] = []
        for agent in agents:
            rules_by_priority = sorted(agent.rules, key=lambda r: -r.priority)
            for rule in rules_by_priority:
                if rule.rule_type is RuleType.MENTION_ONLY:
                    continue
                if rule_matches(rule, message):
                    matched.append(agent.agent_id)
                    break
        return matched

    async def dispatch(self, message: str, agents: list[AgentDispatchInfo]) -> DispatchResult:
        if mentioned := self.match_mentions(message, agents):
            return DispatchResult(agent_ids=mentioned, method=DispatchMethod.MENTION)

        if matched := self.match_deterministic(message, agents):
            return DispatchResult(agent_ids=matched, method=DispatchMethod.DETERMINISTIC)

        if self._llm_fallback is None:
            return DispatchResult(agent_ids=[], method=DispatchMethod.NONE)

        # Graceful degradation (NFR-2.4): callers should catch exceptions from
        # the injected fallback and treat them as DispatchMethod.NONE while
        # surfacing a "routing degraded" warning in the UI.
        llm_matched = await self._llm_fallback(message, agents)
        method = DispatchMethod.LLM if llm_matched else DispatchMethod.NONE
        return DispatchResult(agent_ids=llm_matched, method=method)
