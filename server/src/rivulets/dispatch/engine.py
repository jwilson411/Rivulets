"""Two-stage channel dispatcher (FR-4.1, ADR-005).

Stage 1 — deterministic rule matching over all agents on the channel's
team. Cheap, in-process, must stay under 50ms p95 for 50 agents (NFR-1.1).

Stage 2 — only when no agent's rules matched, an injected LLM fallback
callable decides. The callable is injected rather than called directly
here so this module has zero dependency on a specific LLM provider or on
AgentOS — per ADR-001, agent execution belongs to AgentOS, not to
hand-rolled logic in the App Server.

@mentions (FR-4.5) bypass both stages entirely.

On an agent-originated re-dispatch (FR-5.6), pass `speaker_id` so the
agent that just spoke cannot be selected again by rules or the LLM
fallback. Mentions still can — `@DBA` in Architect's reply is the
reason re-dispatch exists. `always` is a human-turn rule ("answer
everyday chat"), not a license to bounce on every teammate reply.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from rivulets.dispatch.rules import Rule, RuleType, rule_matches

# Rules that may fire on another agent's message. ALWAYS is a human-turn
# participation flag; MENTION_ONLY never matches here (FR-4.5).
_UNSOLICITED_RULE_TYPES = frozenset({RuleType.KEYWORD, RuleType.REGEX, RuleType.SEMANTIC})

_MENTION_RE = re.compile(r"@([A-Za-z0-9_-]+)")


@dataclass(frozen=True, slots=True)
class LlmFallbackResult:
    """What the injected LLM fallback callable actually did. Plain matched
    agent IDs aren't enough for R-4 (#31): `invoked` tells the caller
    whether a real LLM call was attempted, as distinct from a "no match"
    outcome reached without ever calling a provider — e.g. the fallback is
    disabled (dispatcher.fallback_enabled) or no provider is configured
    (dispatch/llm_fallback.py's own short-circuits). Both look identical
    from `agent_ids` alone, but only one of them costs money."""

    agent_ids: list[str]
    invoked: bool


LlmFallback = Callable[[str, list["AgentDispatchInfo"]], Awaitable[LlmFallbackResult]]


@dataclass(frozen=True, slots=True)
class AgentDispatchInfo:
    agent_id: str
    name: str
    rules: list[Rule] = field(default_factory=list[Rule])
    # Only consumed by the LLM fallback (dispatch/llm_fallback.py) — stage
    # 1's deterministic/mention matching never looks at it.
    description: str = ""


class DispatchMethod(StrEnum):
    MENTION = "mention"
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    # Human message, nothing else matched, someone on the team still
    # needs to answer (README: no @mention required). Not used for
    # agent-to-agent recursion — that would ping-pong forever.
    DEFAULT = "default"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    agent_ids: list[str]
    method: DispatchMethod
    # True iff stage 3 made a real LLM call (LlmFallbackResult.invoked) —
    # the event R-4 cares about, since it's the one that costs money.
    # `method` alone can't tell "fallback ran, called an LLM, matched
    # nobody" apart from "fallback wasn't configured/short-circuited" —
    # both report DispatchMethod.NONE.
    llm_invoked: bool = False


def _is_mention_only(agent: AgentDispatchInfo) -> bool:
    return bool(agent.rules) and all(
        rule.rule_type is RuleType.MENTION_ONLY for rule in agent.rules
    )


def _unsolicited_for_agent_reply(agent: AgentDispatchInfo) -> AgentDispatchInfo | None:
    """What this teammate may match against another agent's message.

    Always-only / mention-only / no-rule agents sit the bounce out —
    they answer humans (or an explicit @mention), not every reply.
    Keyword/regex/semantic specialists can still join (Architect says
    "postgresql schema", DBA's keyword fires).
    """
    kept = [rule for rule in agent.rules if rule.rule_type in _UNSOLICITED_RULE_TYPES]
    if not kept:
        return None
    return AgentDispatchInfo(
        agent_id=agent.agent_id,
        name=agent.name,
        rules=kept,
        description=agent.description,
    )


class DispatchEngine:
    def __init__(self, llm_fallback: LlmFallback | None = None) -> None:
        self._llm_fallback = llm_fallback

    def match_mentions(self, message: str, agents: list[AgentDispatchInfo]) -> list[str]:
        mentioned_names = {m.group(1).lower() for m in _MENTION_RE.finditer(message)}
        if not mentioned_names:
            return []
        return [a.agent_id for a in agents if a.name.lower() in mentioned_names]

    def pick_default_teammate(self, agents: list[AgentDispatchInfo]) -> str | None:
        """Who should answer a human message that missed every mention,
        rule, and LLM-fallback pick. Mention-only agents opted out of
        unsolicited dispatch. Prefer a teammate named Assistant (the
        starter generalist); otherwise the first remaining teammate.
        """
        eligible = [a for a in agents if not _is_mention_only(a)]
        if not eligible:
            return None
        for agent in eligible:
            if agent.name.lower() == "assistant":
                return agent.agent_id
        return eligible[0].agent_id

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

    async def dispatch(
        self,
        message: str,
        agents: list[AgentDispatchInfo],
        *,
        speaker_id: str | None = None,
    ) -> DispatchResult:
        if mentioned := self.match_mentions(message, agents):
            return DispatchResult(agent_ids=mentioned, method=DispatchMethod.MENTION)

        if speaker_id is not None:
            # Recursive re-dispatch of an agent's own reply: never hand the
            # speaker back to themselves via always/keyword/LLM, and don't
            # let always-only teammates treat every bounce as a new turn.
            candidates: list[AgentDispatchInfo] = []
            for agent in agents:
                if agent.agent_id == speaker_id:
                    continue
                info = _unsolicited_for_agent_reply(agent)
                if info is not None:
                    candidates.append(info)
            if not candidates:
                return DispatchResult(agent_ids=[], method=DispatchMethod.NONE)
            agents = candidates

        if matched := self.match_deterministic(message, agents):
            return DispatchResult(agent_ids=matched, method=DispatchMethod.DETERMINISTIC)

        if self._llm_fallback is None:
            return DispatchResult(agent_ids=[], method=DispatchMethod.NONE)

        # Graceful degradation (NFR-2.4): callers should catch exceptions from
        # the injected fallback and treat them as DispatchMethod.NONE while
        # surfacing a "routing degraded" warning in the UI.
        fallback_result = await self._llm_fallback(message, agents)
        method = DispatchMethod.LLM if fallback_result.agent_ids else DispatchMethod.NONE
        return DispatchResult(
            agent_ids=fallback_result.agent_ids, method=method, llm_invoked=fallback_result.invoked
        )
