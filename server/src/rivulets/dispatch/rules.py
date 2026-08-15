"""Deterministic routing rule types and matching.

- keyword: trigger if the message contains any of `pattern` (case-insensitive)
- regex: trigger if the message matches `pattern`
- semantic: trigger if the message contains any of `pattern` trigger phrases
  (a cheap substring heuristic today; upgradeable to embedding similarity
  without changing the Rule shape)
- always: the agent responds to every message (e.g. an orchestrator)
- mention_only: the agent only responds to an explicit @mention, never via
  dispatch matching
"""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class RuleType(StrEnum):
    KEYWORD = "keyword"
    REGEX = "regex"
    SEMANTIC = "semantic"
    ALWAYS = "always"
    MENTION_ONLY = "mention_only"


@dataclass(frozen=True, slots=True)
class Rule:
    rule_type: RuleType
    # Unused for ALWAYS/MENTION_ONLY, hence the default.
    pattern: list[str] | str = ""
    priority: int = 0


def is_valid_regex(pattern: str) -> bool:
    """True iff `pattern` compiles as a Python regex. Shared by every layer
    that persists a REGEX rule (LLM generation in rule_generation.py, the
    update_agent_routing_rules tool in service.py, the HTTP routing-rules
    endpoint in api/agents.py) so an invalid pattern is caught before it
    ever reaches rule_matches below (#366)."""
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def rule_matches(rule: Rule, message: str) -> bool:
    """Evaluate a single rule against a message. Pure and side-effect free
    so the dispatcher can run this over N agents' rules in-process, in
    priority order, well under the 50ms budget (NFR-1.1)."""
    match rule.rule_type:
        case RuleType.ALWAYS:
            return True
        case RuleType.MENTION_ONLY:
            return False  # only ever invoked via explicit @mention (FR-4.5)
        case RuleType.KEYWORD:
            keywords = rule.pattern if isinstance(rule.pattern, list) else [rule.pattern]
            lowered = message.lower()
            return any(kw.lower() in lowered for kw in keywords)
        case RuleType.REGEX:
            pattern = rule.pattern if isinstance(rule.pattern, str) else rule.pattern[0]
            try:
                return re.search(pattern, message) is not None
            except re.error:
                # Stored rules are validated before persisting (is_valid_regex
                # above), but this must still never raise for rows written
                # before that validation existed, or written directly to the
                # DB (#366) -- one bad pattern must not 500 the whole
                # dispatch pass for every other agent on the channel.
                logger.warning(
                    "Invalid regex routing rule pattern %r; treating as no-match", pattern
                )
                return False
        case RuleType.SEMANTIC:
            phrases = rule.pattern if isinstance(rule.pattern, list) else [rule.pattern]
            lowered = message.lower()
            return any(phrase.lower() in lowered for phrase in phrases)
