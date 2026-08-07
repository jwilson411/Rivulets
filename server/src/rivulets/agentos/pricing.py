"""Static $/1M-token pricing for cost estimation on the usage dashboard
(#28) and per-agent run history (FR-3.5).

agno's own `RunMetrics.cost` (agno.metrics.BaseMetrics) is populated by only
a couple of model integrations (Gemini, some OpenAI paths) and is never set
for Anthropic/DeepSeek — the providers this app actually ships against — so
there's no live-metered cost to read back. This table is a hand-maintained,
best-effort estimate instead: prices drift as providers change them, and a
model not listed here (a brand new release, or any `openai_compatible`
self-hosted endpoint, whose pricing isn't knowable in advance) has no known
cost at all rather than a wrong guess.

Keyed by the exact `(provider, model_name)` pair parsed out of the
'provider:model_name' strings already used everywhere (`Agent.model`,
`model_used` in `Message.metadata_json`, `_DEFAULT_TIER_MODELS` in
agentos/models.py).
"""

# (input $/1M tokens, output $/1M tokens)
_PRICING_USD_PER_MILLION: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-haiku-4-5-20251001"): (1.00, 5.00),
    ("anthropic", "claude-3-5-haiku-latest"): (0.80, 4.00),
    ("anthropic", "claude-opus-4-1"): (15.00, 75.00),
    ("anthropic", "claude-opus-5"): (15.00, 75.00),
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("openai", "gpt-4o"): (2.50, 10.00),
    ("openai", "o3-mini"): (1.10, 4.40),
    ("deepseek", "deepseek-chat"): (0.27, 1.10),
    ("deepseek", "deepseek-reasoner"): (0.55, 2.19),
}


def estimate_cost_usd(
    provider: str, model_name: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Best-effort cost in USD for one run, or None if `provider:model_name`
    isn't in the static table above (unpriced, not free)."""
    prices = _PRICING_USD_PER_MILLION.get((provider, model_name))
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
