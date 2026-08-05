"""Maps our stored `agent.model` string ("provider:model_name",
data-model.md) plus a configured `provider_config` row to the matching
agno Model class. Covers FR-1.4's minimum provider set: OpenAI, Anthropic,
DeepSeek, and any OpenAI-compatible endpoint.
"""

from agno.models.anthropic import Claude
from agno.models.base import Model
from agno.models.deepseek import DeepSeek
from agno.models.openai import OpenAIChat
from agno.models.openai.like import OpenAILike
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import ProviderConfig
from rivulets.security.credentials import get_provider_key

_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


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
