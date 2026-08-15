"""dispatch/rules.py's rule_matches -- ALWAYS/KEYWORD/REGEX are already
exercised end to end via test_dispatch.py and test_rivulet_dispatch.py, but
nothing creates a MENTION_ONLY or SEMANTIC rule and calls rule_matches on it
directly (mention-only agents are only ever invoked through the separate
@mention path, never through rule matching itself)."""

from rivulets.dispatch.rules import Rule, RuleType, is_valid_regex, rule_matches

_BAD_REGEX = r"\b(https?://[\w-]+(\.[\w-]+)+(\/[\w- ./?%&=]*)?)"  # #366: bad char range \w-


def test_regex_rule_matches() -> None:
    rule = Rule(RuleType.REGEX, r"ORD-\d+")
    assert rule_matches(rule, "status of ORD-4821?") is True
    assert rule_matches(rule, "no order number here") is False


def test_invalid_regex_rule_never_raises_and_treated_as_no_match() -> None:
    """#366: one bad regex rule on any agent must not 500 dispatch for the
    whole channel -- rule_matches degrades to "no match" instead of
    propagating re.error."""
    rule = Rule(RuleType.REGEX, _BAD_REGEX)
    assert rule_matches(rule, "check out https://example.com/path") is False
    assert rule_matches(rule, "hello") is False


def test_is_valid_regex() -> None:
    assert is_valid_regex(r"ORD-\d+") is True
    assert is_valid_regex(_BAD_REGEX) is False


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
