"""Auto mode's (#23) message complexity classifier. Mirrors
test_llm_fallback.py's monkeypatch pattern: resolve_model and the isolated
_run_classification seam are mocked so these run without a real
provider/LLM call.
"""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Agent, AgentRun, ProviderConfig
from rivulets.dispatch.complexity_classifier import (
    _ComplexityDecision,  # pyright: ignore[reportPrivateUsage]
    _run_classification,  # pyright: ignore[reportPrivateUsage]
    classify_tier,
)

_AGENT_FIELDS = {
    "description": "A test agent used only in complexity classifier tests.",
    "instructions": "Do the thing.",
    "model": "auto",
}


async def _make_agent(db_session: AsyncSession, agent_id: str = "agent-1") -> Agent:
    agent = Agent(id=agent_id, name=f"Agent {agent_id}", **_AGENT_FIELDS)
    db_session.add(agent)
    await db_session.commit()
    return agent


class _FakeRunOutput:
    def __init__(self, content: object, *, input_tokens: int = 10, output_tokens: int = 5) -> None:
        self.content = content
        self.metrics = _FakeMetrics(input_tokens, output_tokens)


class _FakeMetrics:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


def _fake_agno_agent(run_output_content: object) -> type:
    class _FakeAgnoAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def arun(self, *_args: object, **_kwargs: object) -> _FakeRunOutput:
            return _FakeRunOutput(run_output_content)

    return _FakeAgnoAgent


async def test_classify_tier_defaults_to_cheap_with_no_provider(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    assert await classify_tier(db_session, agent, "What's 2+2?") == "cheap"


async def test_classify_tier_defaults_to_cheap_when_resolve_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    agent = await _make_agent(db_session)

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)

    result = await classify_tier(db_session, agent, "Design a distributed consensus protocol.")
    assert result == "cheap"


async def test_classify_tier_defaults_to_cheap_when_classification_returns_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    agent = await _make_agent(db_session)

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> Any:
        return _FakeRunOutput(None)

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    assert await classify_tier(db_session, agent, "anything") == "cheap"


async def test_classify_tier_returns_capable_when_classifier_says_so(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    agent = await _make_agent(db_session)

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> Any:
        return _FakeRunOutput(_ComplexityDecision(tier="capable"))

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    assert await classify_tier(db_session, agent, "Prove P != NP.") == "capable"


async def test_classify_tier_runs_on_the_cheap_tier_model_even_when_it_decides_capable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classifier itself must never run on the capable-tier model --
    that would defeat the point of classifying cheaply first."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    agent = await _make_agent(db_session)

    resolved_provider_models: list[str] = []

    async def fake_resolve_model(_db: object, provider_model: str) -> object:
        resolved_provider_models.append(provider_model)
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> Any:
        return _FakeRunOutput(_ComplexityDecision(tier="capable"))

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    await classify_tier(db_session, agent, "Prove P != NP.")
    assert resolved_provider_models == ["anthropic:claude-haiku-4-5-20251001"]


async def test_classify_tier_records_dispatcher_call_spend(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#246: a completed classification call must show up as an AgentRun
    attributed to the agent it's classifying for, source='dispatcher_call',
    so its tokens/cost stop being invisible to usage/budgets."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    agent = await _make_agent(db_session)

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_classification(*_args: object, **_kwargs: object) -> Any:
        return _FakeRunOutput(_ComplexityDecision(tier="capable"))

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier._run_classification", fake_run_classification
    )

    await classify_tier(db_session, agent, "Prove P != NP.")

    run = (await db_session.execute(select(AgentRun))).scalars().one()
    assert run.agent_id == agent.id
    assert run.source == "dispatcher_call"
    assert run.input_tokens == 10
    assert run.output_tokens == 5


async def test_classify_tier_does_not_record_when_resolve_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    agent = await _make_agent(db_session)

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr("rivulets.dispatch.complexity_classifier.resolve_model", fake_resolve_model)

    await classify_tier(db_session, agent, "anything")

    runs = (await db_session.execute(select(AgentRun))).scalars().all()
    assert runs == []


async def test_run_classification_returns_the_raw_run_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real seam llm_fallback/complexity_classifier both isolate for
    tests -- this is the one test that actually calls through it, faking
    only agno's AgnoAgent class itself (not the seam), so the real
    instantiate-call-unwrap logic in _run_classification runs for real.
    _run_classification itself now returns the raw RunOutput (#246), not
    the parsed decision -- classify_tier does that unwrapping."""
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.AgnoAgent",
        _fake_agno_agent(_ComplexityDecision(tier="capable")),
    )

    result = await _run_classification(object(), "a hard question")  # type: ignore[arg-type]

    assert result.content == _ComplexityDecision(tier="capable")


async def test_run_classification_content_can_be_an_unexpected_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agno's output_schema coercion is trusted but not blindly -- content
    that isn't a _ComplexityDecision instance (a malformed/failed
    structured-output call) is passed through as-is; classify_tier is the
    one that turns that into a `cheap` degrade, not this seam."""
    monkeypatch.setattr(
        "rivulets.dispatch.complexity_classifier.AgnoAgent", _fake_agno_agent("not a decision")
    )

    result = await _run_classification(object(), "anything")  # type: ignore[arg-type]

    assert result.content == "not a decision"
