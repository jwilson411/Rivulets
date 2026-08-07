"""dispatch/rules.py's rule_matches -- ALWAYS/KEYWORD/REGEX are already
exercised end to end via test_dispatch.py and test_rivulet_dispatch.py, but
nothing creates a MENTION_ONLY or SEMANTIC rule and calls rule_matches on it
directly (mention-only agents are only ever invoked through the separate
@mention path, never through rule matching itself)."""

from rivulets.dispatch.rules import Rule, RuleType, rule_matches


def test_mention_only_rule_never_matches_via_rule_matching() -> None:
    """MENTION_ONLY agents only ever respond via an explicit @mention
    (FR-4.5), handled by a different code path entirely -- rule_matches
    itself must always say no for this rule type."""
    rule = Rule(RuleType.MENTION_ONLY)
    assert rule_matches(rule, "hey can you help me?") is False
    assert rule_matches(rule, "") is False


def test_semantic_rule_matches_any_configured_phrase() -> None:
    rule = Rule(RuleType.SEMANTIC, ["order status", "where is my package"])
    assert rule_matches(rule, "Can you tell me my order status please?") is True
    assert rule_matches(rule, "WHERE IS MY PACKAGE") is True  # case-insensitive
    assert rule_matches(rule, "what's the weather like") is False


def test_semantic_rule_accepts_a_single_string_pattern_too() -> None:
    rule = Rule(RuleType.SEMANTIC, "refund")
    assert rule_matches(rule, "I'd like a refund please") is True
    assert rule_matches(rule, "hello") is False
