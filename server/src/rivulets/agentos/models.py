"""Maps our stored `agent.model` string ("provider:model_name",
data-model.md) plus a configured `provider_config` row to the matching
agno Model class. Covers FR-1.4's provider set: OpenAI, Anthropic,
DeepSeek, Google (Gemini), Mistral, Groq, xAI (Grok), Qwen (via DashScope),
Ollama, Cohere, and any other OpenAI-compatible endpoint.

Also resolves the two "Auto" mode tiers (#23) — `cheap` and `capable` — to
a concrete `provider:model_name` string. `resolve_default_provider` is
shared with dispatch/rule_generation.py's `pick_dispatcher_model`, which
answers the same "which provider backs cheap, dispatcher-adjacent LLM
calls" question for routing-rule generation and LLM dispatch fallback.
"""

import json
from typing import Literal

from agno.models.anthropic import Claude
from agno.models.base import Model
from agno.models.cohere import Cohere
from agno.models.dashscope import DashScope
from agno.models.deepseek import DeepSeek
from agno.models.google import Gemini
from agno.models.groq import Groq
from agno.models.mistral import MistralChat
from agno.models.ollama import Ollama
from agno.models.openai import OpenAIChat
from agno.models.openai.like import OpenAILike
from agno.models.xai import xAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import ProviderConfig, WorkspaceSetting
from rivulets.security.credentials import get_provider_key

_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_QWEN_DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# The `agent.model` sentinel for "Auto" mode (#23): the model is resolved
# fresh per-message (dispatch/service.py) instead of once at agent build
# time. Stored directly in the existing unconstrained `model` column
# rather than a new one — there's no live migration tooling in this
# project (init_db() only does create_all), so it round-trips through the
# generic peer-sync layer (sync/apply.py) with zero special-casing.
AUTO_MODEL = "auto"

ModelTier = Literal["cheap", "capable"]

# Default model per provider per tier. `capable` intentionally maps to
# Opus, not Fable — Fable is priced 2x Opus and is opt-in-only per
# Anthropic's own guidance (elevated safety classifiers that can outright
# refuse, different data-retention requirements), so it would be wrong to
# route to it automatically/silently. A workspace that wants Fable for its
# hardest traffic sets it explicitly via the `model_tiers.override` setting.
_DEFAULT_TIER_MODELS: dict[str, dict[ModelTier, str]] = {
    "anthropic": {"cheap": "claude-haiku-4-5-20251001", "capable": "claude-opus-5"},
    "openai": {"cheap": "gpt-4o-mini", "capable": "gpt-4o"},
    "deepseek": {"cheap": "deepseek-chat", "capable": "deepseek-chat"},
}


class UnknownProviderError(ValueError):
    pass


def parse_provider_model(value: str) -> tuple[str, str]:
    """Split 'provider:model_name' into its parts."""
    provider, _, model_name = value.partition(":")
    if not provider or not model_name:
        raise ValueError(f"agent.model must be 'provider:model_name', got {value!r}")
    return provider, model_name


def build_model(provider: str, model_name: str, api_key: str, base_url: str | None = None) -> Model:
    if provider == "anthropic":
        return Claude(id=model_name, api_key=api_key)
    if provider == "openai":
        return OpenAIChat(id=model_name, api_key=api_key, base_url=base_url)
    if provider == "deepseek":
        return DeepSeek(
            id=model_name, api_key=api_key, base_url=base_url or _DEEPSEEK_DEFAULT_BASE_URL
        )
    if provider == "google":
        return Gemini(id=model_name, api_key=api_key)
    if provider == "mistral":
        return MistralChat(id=model_name, api_key=api_key)
    if provider == "groq":
        return Groq(id=model_name, api_key=api_key, base_url=base_url)
    if provider == "xai":
        return xAI(id=model_name, api_key=api_key, base_url=base_url or "https://api.x.ai/v1")
    if provider == "qwen":
        # DashScope's OpenAI-compatible endpoint — same shape `openai_compatible`
        # users already reach Qwen through manually, now first-class with a
        # sane default base_url instead of requiring one.
        return DashScope(
            id=model_name, api_key=api_key, base_url=base_url or _QWEN_DEFAULT_BASE_URL
        )
    if provider == "cohere":
        return Cohere(id=model_name, api_key=api_key)
    if provider == "ollama":
        # Local models: no API key required, just a host. An empty string
        # (the UI's "no key" submission) is treated as "not provided" so
        # Ollama's own OLLAMA_API_KEY env fallback still applies.
        if not base_url:
            raise ValueError("The 'ollama' provider requires a base_url")
        return Ollama(id=model_name, api_key=api_key or None, host=base_url)
    if provider == "openai_compatible":
        if not base_url:
            raise ValueError("The 'openai_compatible' provider requires a base_url")
        return OpenAILike(id=model_name, api_key=api_key, base_url=base_url)
    raise UnknownProviderError(f"Unknown provider: {provider!r}")


async def resolve_model(db: AsyncSession, provider_model: str) -> Model:
    """Look up the configured provider + credential for an agent's `model`
    field and build the matching agno Model instance."""
    provider, model_name = parse_provider_model(provider_model)
    config = await db.scalar(select(ProviderConfig).where(ProviderConfig.provider == provider))
    if config is None:
        raise UnknownProviderError(
            f"No provider configured for '{provider}'. Add it in Settings > Providers."
        )
    api_key = get_provider_key(config.api_key_ref)
    return build_model(provider, model_name, api_key, config.base_url)


async def resolve_default_provider(db: AsyncSession) -> ProviderConfig | None:
    """The workspace's default provider (or its first configured one, if
    none is marked default), or None if no provider is configured at all."""
    result = await db.execute(select(ProviderConfig))
    configs = result.scalars().all()
    if not configs:
        return None
    return next((c for c in configs if c.is_default), configs[0])


async def resolve_tier_model(db: AsyncSession, tier: ModelTier) -> str | None:
    """Resolve an Auto-mode tier ('cheap' or 'capable') to a concrete
    'provider:model_name' string: a workspace's `model_tiers.override`
    setting (JSON `{"cheap": ..., "capable": ...}`, merged over the
    computed default) wins if it names this tier, otherwise the default
    provider's tier model is used. Returns None if no provider is
    configured, or the default provider has no default model for this
    tier (e.g. `openai_compatible`, whose models aren't known in advance)."""
    provider = await resolve_default_provider(db)
    default = None
    if provider is not None:
        model_name = _DEFAULT_TIER_MODELS.get(provider.provider, {}).get(tier)
        if model_name is not None:
            default = f"{provider.provider}:{model_name}"

    override_row = await db.get(WorkspaceSetting, "model_tiers.override")
    if override_row is not None:
        overrides: dict[str, str] = json.loads(override_row.value) or {}
        if tier in overrides:
            return str(overrides[tier])
    return default
