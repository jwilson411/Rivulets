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
    _run_classification,  # pyright: ignore[reportPrivateUsage]
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

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)

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

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
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

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
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

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    await classify_tier(db_session, "Prove P != NP.")
    assert resolved_provider_models == ["anthropic:claude-haiku-4-5-20251001"]


class _FakeRunOutput:
    def __init__(self, content: object) -> None:
        self.content = content


def _fake_agno_agent(run_output_content: object) -> type:
    class _FakeAgnoAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def arun(self, *_args: object, **_kwargs: object) -> _FakeRunOutput:
            return _FakeRunOutput(run_output_content)

    return _FakeAgnoAgent


async def test_run_classification_returns_the_decision_from_the_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real seam llm_fallback/complexity_classifier both isolate for
    tests -- this is the one test that actually calls through it, faking
    only agno's AgnoAgent class itself (not the seam), so the real
    instantiate-call-unwrap logic in _run_classification runs for real."""
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.AgnoAgent",
        _fake_agno_agent(_ComplexityDecision(tier="capable")),
    )

    result = await _run_classification(object(), "a hard question")  # type: ignore[arg-type]

    assert result == _ComplexityDecision(tier="capable")


async def test_run_classification_returns_none_for_unexpected_content_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agno's output_schema coercion is trusted but not blindly -- content
    that isn't a _ComplexityDecision instance (a malformed/failed
    structured-output call) must come back as None, not be passed through."""
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.AgnoAgent", _fake_agno_agent("not a decision")
    )

    result = await _run_classification(object(), "anything")  # type: ignore[arg-type]

    assert result is None
