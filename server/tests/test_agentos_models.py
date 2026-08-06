import json

import pytest
from agno.models.anthropic import Claude
from agno.models.deepseek import DeepSeek
from agno.models.openai import OpenAIChat
from agno.models.openai.like import OpenAILike
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import (
    UnknownProviderError,
    build_model,
    parse_provider_model,
    resolve_default_provider,
    resolve_model,
    resolve_tier_model,
)
from rivulets.db.models import ProviderConfig, WorkspaceSetting


def test_parse_provider_model_splits_on_first_colon() -> None:
    assert parse_provider_model("anthropic:claude-3-5-haiku-latest") == (
        "anthropic",
        "claude-3-5-haiku-latest",
    )


@pytest.mark.parametrize("value", ["no-colon", ":missing-provider", "missing-model:"])
def test_parse_provider_model_rejects_malformed_input(value: str) -> None:
    with pytest.raises(ValueError, match="provider:model_name"):
        parse_provider_model(value)


def test_build_model_anthropic() -> None:
    model = build_model("anthropic", "claude-3-5-haiku-latest", "sk-fake")
    assert isinstance(model, Claude)
    assert model.id == "claude-3-5-haiku-latest"


def test_build_model_openai() -> None:
    model = build_model("openai", "gpt-4o-mini", "sk-fake")
    assert isinstance(model, OpenAIChat)
    assert model.id == "gpt-4o-mini"


def test_build_model_deepseek_defaults_base_url() -> None:
    model = build_model("deepseek", "deepseek-chat", "sk-fake")
    assert isinstance(model, DeepSeek)
    assert model.base_url == "https://api.deepseek.com"


def test_build_model_openai_compatible_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        build_model("openai_compatible", "local-model", "sk-fake")


def test_build_model_openai_compatible_with_base_url() -> None:
    model = build_model(
        "openai_compatible", "local-model", "sk-fake", base_url="http://localhost:11434/v1"
    )
    assert isinstance(model, OpenAILike)


def test_build_model_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        build_model("made-up-provider", "some-model", "sk-fake")


def _fake_get_provider_key(_ref: str) -> str:
    return "sk-from-keychain"


async def test_resolve_model_looks_up_provider_config(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.agentos.models.get_provider_key", _fake_get_provider_key)
    db_session.add(
        ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="provider-key:test-id")
    )
    await db_session.commit()

    model = await resolve_model(db_session, "anthropic:claude-3-5-haiku-latest")
    assert isinstance(model, Claude)
    assert model.id == "claude-3-5-haiku-latest"


async def test_resolve_model_raises_when_provider_not_configured(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(UnknownProviderError, match="No provider configured"):
        await resolve_model(db_session, "anthropic:claude-3-5-haiku-latest")


async def test_resolve_default_provider_returns_none_with_no_providers(
    db_session: AsyncSession,
) -> None:
    assert await resolve_default_provider(db_session) is None


async def test_resolve_default_provider_prefers_is_default(db_session: AsyncSession) -> None:
    db_session.add(
        ProviderConfig(provider="openai", label="OpenAI", api_key_ref="ref-1", is_default=False)
    )
    db_session.add(
        ProviderConfig(
            provider="anthropic", label="Anthropic", api_key_ref="ref-2", is_default=True
        )
    )
    await db_session.commit()

    provider = await resolve_default_provider(db_session)
    assert provider is not None
    assert provider.provider == "anthropic"


async def test_resolve_default_provider_falls_back_to_first_when_no_default(
    db_session: AsyncSession,
) -> None:
    db_session.add(ProviderConfig(provider="deepseek", label="DeepSeek", api_key_ref="ref-1"))
    await db_session.commit()

    provider = await resolve_default_provider(db_session)
    assert provider is not None
    assert provider.provider == "deepseek"


async def test_resolve_tier_model_returns_none_with_no_providers(
    db_session: AsyncSession,
) -> None:
    assert await resolve_tier_model(db_session, "cheap") is None


async def test_resolve_tier_model_uses_default_provider_per_tier(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"),
    )
    await db_session.commit()

    assert await resolve_tier_model(db_session, "cheap") == "anthropic:claude-haiku-4-5-20251001"
    assert await resolve_tier_model(db_session, "capable") == "anthropic:claude-opus-5"


async def test_resolve_tier_model_returns_none_for_provider_with_no_tier_default(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        ProviderConfig(provider="openai_compatible", label="Local", api_key_ref="ref-1"),
    )
    await db_session.commit()

    assert await resolve_tier_model(db_session, "cheap") is None


async def test_resolve_tier_model_honors_workspace_override(db_session: AsyncSession) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    db_session.add(
        WorkspaceSetting(
            key="model_tiers.override", value=json.dumps({"capable": "openai:gpt-4o"})
        )
    )
    await db_session.commit()

    # Overridden tier uses the override; the other tier still falls
    # through to the computed default.
    assert await resolve_tier_model(db_session, "capable") == "openai:gpt-4o"
    assert await resolve_tier_model(db_session, "cheap") == "anthropic:claude-haiku-4-5-20251001"
