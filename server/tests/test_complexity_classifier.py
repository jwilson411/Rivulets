"""Auto mode's (#23) message complexity classifier. Mirrors
test_llm_fallback.py's monkeypatch pattern: resolve_model and the isolated
_run_classification seam are mocked so these run without a real
provider/LLM call.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import ProviderConfig
from rivulets.dispatch.complexity_classifier import (
    _ComplexityDecision,  # pyright: ignore[reportPrivateUsage]
    classify_tier,
)


async def test_classify_tier_defaults_to_cheap_with_no_provider(db_session: AsyncSession) -> None:
    assert await classify_tier(db_session, "What's 2+2?") == "cheap"


async def test_classify_tier_defaults_to_cheap_when_resolve_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model
    )

    assert await classify_tier(db_session, "Design a distributed consensus protocol.") == "cheap"


async def test_classify_tier_defaults_to_cheap_when_classification_returns_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model
    )
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    assert await classify_tier(db_session, "anything") == "cheap"


async def test_classify_tier_returns_capable_when_classifier_says_so(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> _ComplexityDecision:
        return _ComplexityDecision(tier="capable")

    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model
    )
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    assert await classify_tier(db_session, "Prove P != NP.") == "capable"


async def test_classify_tier_runs_on_the_cheap_tier_model_even_when_it_decides_capable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classifier itself must never run on the capable-tier model --
    that would defeat the point of classifying cheaply first."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    resolved_provider_models: list[str] = []

    async def fake_resolve_model(_db: object, provider_model: str) -> object:
        resolved_provider_models.append(provider_model)
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> _ComplexityDecision:
        return _ComplexityDecision(tier="capable")

    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model
    )
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    await classify_tier(db_session, "Prove P != NP.")
    assert resolved_provider_models == ["anthropic:claude-haiku-4-5-20251001"]
