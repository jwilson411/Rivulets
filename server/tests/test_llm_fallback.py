"""LLM dispatch fallback (ADR-005 stage 2). Mirrors test_rule_generation.py's
monkeypatch pattern: resolve_model and the isolated _run_decision seam are
mocked so these run without a real provider/LLM call.
"""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import ProviderConfig, WorkspaceSetting
from rivulets.dispatch.engine import AgentDispatchInfo
from rivulets.dispatch.llm_fallback import (
    _DispatchDecision,  # pyright: ignore[reportPrivateUsage]
    build_llm_fallback,
)

_AGENTS = [
    AgentDispatchInfo(agent_id="dba-1", name="DBA", description="Handles database questions."),
    AgentDispatchInfo(
        agent_id="frontend-1", name="Frontend", description="Handles UI/CSS questions."
    ),
]


async def test_llm_fallback_returns_empty_with_no_provider(db_session: AsyncSession) -> None:
    fallback = build_llm_fallback(db_session)
    assert await fallback("How do I optimize this SQL query?", _AGENTS) == []


async def test_llm_fallback_disabled_via_workspace_setting_skips_the_llm_call(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    db_session.add(WorkspaceSetting(key="dispatcher.fallback_enabled", value=json.dumps(False)))
    await db_session.commit()

    called = False

    async def fake_pick_dispatcher_model(*_args: object, **_kwargs: object) -> str:
        nonlocal called
        called = True
        return "anthropic:claude-haiku-4-5-20251001"

    monkeypatch.setattr(
        "rivulets.dispatch.llm_fallback.pick_dispatcher_model", fake_pick_dispatcher_model
    )

    fallback = build_llm_fallback(db_session)
    assert await fallback("How do I optimize this SQL query?", _AGENTS) == []
    assert called is False


async def test_llm_fallback_defaults_to_enabled_when_setting_is_unset(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_decision(*_args: object, **_kwargs: object) -> _DispatchDecision:
        return _DispatchDecision(agent_names=["DBA"])

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.dispatch.llm_fallback._run_decision", fake_run_decision)

    fallback = build_llm_fallback(db_session)
    assert await fallback("query help please", _AGENTS) == ["dba-1"]


async def test_llm_fallback_returns_empty_when_resolve_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)

    fallback = build_llm_fallback(db_session)
    assert await fallback("How do I optimize this SQL query?", _AGENTS) == []


async def test_llm_fallback_maps_agent_names_back_to_ids(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()  # never touched -- _run_decision is mocked below too

    async def fake_run_decision(*_args: object, **_kwargs: object) -> _DispatchDecision:
        return _DispatchDecision(agent_names=["DBA"])

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.dispatch.llm_fallback._run_decision", fake_run_decision)

    fallback = build_llm_fallback(db_session)
    matched = await fallback("How do I optimize this SQL query?", _AGENTS)
    assert matched == ["dba-1"]


async def test_llm_fallback_is_case_insensitive_on_agent_name(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_decision(*_args: object, **_kwargs: object) -> _DispatchDecision:
        return _DispatchDecision(agent_names=["dba"])

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.dispatch.llm_fallback._run_decision", fake_run_decision)

    fallback = build_llm_fallback(db_session)
    assert await fallback("query help please", _AGENTS) == ["dba-1"]


async def test_llm_fallback_drops_hallucinated_agent_names(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name the LLM invents that doesn't match any real agent on the
    team must be dropped, not surfaced as a bogus agent_id."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_decision(*_args: object, **_kwargs: object) -> _DispatchDecision:
        return _DispatchDecision(agent_names=["DBA", "NotOnThisTeam"])

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.dispatch.llm_fallback._run_decision", fake_run_decision)

    fallback = build_llm_fallback(db_session)
    assert await fallback("query help please", _AGENTS) == ["dba-1"]


async def test_llm_fallback_returns_empty_when_nothing_matches(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_decision(*_args: object, **_kwargs: object) -> _DispatchDecision:
        return _DispatchDecision(agent_names=[])

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.dispatch.llm_fallback._run_decision", fake_run_decision)

    fallback = build_llm_fallback(db_session)
    assert await fallback("totally unrelated message", _AGENTS) == []


async def test_llm_fallback_returns_empty_when_decision_is_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_decision returns None when the model's output didn't parse
    into the expected schema -- must degrade gracefully, not raise."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_decision(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("rivulets.dispatch.llm_fallback.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.dispatch.llm_fallback._run_decision", fake_run_decision)

    fallback = build_llm_fallback(db_session)
    assert await fallback("totally unrelated message", _AGENTS) == []
