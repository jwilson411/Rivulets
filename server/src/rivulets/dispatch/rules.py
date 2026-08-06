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

import re
from dataclasses import dataclass
from enum import StrEnum


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
            return re.search(pattern, message) is not None
        case RuleType.SEMANTIC:
            phrases = rule.pattern if isinstance(rule.pattern, list) else [rule.pattern]
            lowered = message.lower()
            return any(phrase.lower() in lowered for phrase in phrases)
