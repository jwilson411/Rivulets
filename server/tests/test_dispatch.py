import pytest

from rivulets.dispatch.engine import (
    AgentDispatchInfo,
    DispatchEngine,
    DispatchMethod,
    LlmFallbackResult,
)
from rivulets.dispatch.rules import Rule, RuleType


def _dba_agent() -> AgentDispatchInfo:
    return AgentDispatchInfo(
        agent_id="dba-1",
        name="DBA",
        rules=[
            Rule(RuleType.KEYWORD, ["postgresql", "schema", "sql"], priority=10),
            Rule(RuleType.REGEX, r"(?i)\bpostgres\b", priority=8),
        ],
    )


def _orchestrator_agent() -> AgentDispatchInfo:
    return AgentDispatchInfo(agent_id="orch-1", name="Orchestrator", rules=[Rule(RuleType.ALWAYS)])


def _mention_only_agent() -> AgentDispatchInfo:
    return AgentDispatchInfo(
        agent_id="silent-1", name="Silent", rules=[Rule(RuleType.MENTION_ONLY)]
    )


@pytest.mark.asyncio
async def test_deterministic_keyword_match() -> None:
    engine = DispatchEngine()
    result = await engine.dispatch(
        "I need help designing a PostgreSQL schema for user profiles.",
        [_dba_agent()],
    )
    assert result.method is DispatchMethod.DETERMINISTIC
    assert result.agent_ids == ["dba-1"]


@pytest.mark.asyncio
async def test_no_match_with_no_fallback_is_none() -> None:
    engine = DispatchEngine()
    result = await engine.dispatch("What's the weather like today?", [_dba_agent()])
    assert result.method is DispatchMethod.NONE
    assert result.agent_ids == []


@pytest.mark.asyncio
async def test_mention_bypasses_rules_entirely() -> None:
    engine = DispatchEngine()
    result = await engine.dispatch("Hey @DBA what's up?", [_dba_agent()])
    assert result.method is DispatchMethod.MENTION
    assert result.agent_ids == ["dba-1"]


@pytest.mark.asyncio
async def test_mention_only_agent_never_matches_deterministically() -> None:
    engine = DispatchEngine()
    result = await engine.dispatch("Can someone help me?", [_mention_only_agent()])
    assert result.agent_ids == []

    mentioned = await engine.dispatch("@Silent are you there?", [_mention_only_agent()])
    assert mentioned.agent_ids == ["silent-1"]


@pytest.mark.asyncio
async def test_always_rule_matches_every_message() -> None:
    engine = DispatchEngine()
    result = await engine.dispatch("literally anything", [_orchestrator_agent()])
    assert result.method is DispatchMethod.DETERMINISTIC
    assert result.agent_ids == ["orch-1"]


@pytest.mark.asyncio
async def test_llm_fallback_used_when_no_deterministic_match() -> None:
    async def fake_llm(message: str, agents: list[AgentDispatchInfo]) -> LlmFallbackResult:
        return LlmFallbackResult(
            agent_ids=[a.agent_id for a in agents if "overfit" in message], invoked=True
        )

    engine = DispatchEngine(llm_fallback=fake_llm)
    agent = AgentDispatchInfo(agent_id="ds-1", name="DataScientist", rules=[])
    result = await engine.dispatch(
        "I'm trying to figure out why my model keeps overfitting.", [agent]
    )
    assert result.method is DispatchMethod.LLM
    assert result.agent_ids == ["ds-1"]
    assert result.llm_invoked is True


@pytest.mark.asyncio
async def test_llm_fallback_not_invoked_reports_none_without_cost() -> None:
    """R-4 (#31): a fallback that short-circuits (disabled, no provider
    configured) before ever calling an LLM must be distinguishable from one
    that called an LLM and simply matched nobody — dispatch/service.py's
    hit-rate tracking depends on `llm_invoked` alone, since `method` reports
    DispatchMethod.NONE for both."""

    async def fake_llm(message: str, agents: list[AgentDispatchInfo]) -> LlmFallbackResult:
        return LlmFallbackResult(agent_ids=[], invoked=False)

    engine = DispatchEngine(llm_fallback=fake_llm)
    agent = AgentDispatchInfo(agent_id="ds-1", name="DataScientist", rules=[])
    result = await engine.dispatch("anything at all", [agent])
    assert result.method is DispatchMethod.NONE
    assert result.llm_invoked is False


def test_default_teammate_prefers_assistant_and_skips_mention_only() -> None:
    engine = DispatchEngine()
    silent = _mention_only_agent()
    dba = _dba_agent()
    assistant = AgentDispatchInfo(agent_id="asst-1", name="Assistant", rules=[])
    assert engine.pick_default_teammate([silent, dba, assistant]) == "asst-1"
    assert engine.pick_default_teammate([silent, dba]) == "dba-1"
    assert engine.pick_default_teammate([silent]) is None


@pytest.mark.asyncio
async def test_llm_fallback_invoked_but_no_match_still_counts_as_invoked() -> None:
    async def fake_llm(message: str, agents: list[AgentDispatchInfo]) -> LlmFallbackResult:
        return LlmFallbackResult(agent_ids=[], invoked=True)

    engine = DispatchEngine(llm_fallback=fake_llm)
    agent = AgentDispatchInfo(agent_id="ds-1", name="DataScientist", rules=[])
    result = await engine.dispatch("anything at all", [agent])
    assert result.method is DispatchMethod.NONE
    assert result.llm_invoked is True


@pytest.mark.asyncio
async def test_speaker_is_excluded_from_unsolicited_redispatch() -> None:
    """An always-rule agent answering a human must not then match its own
    reply — that's the Assistant self-loop the cycle guard used to catch
    after the damage was done."""
    llm_calls: list[list[str]] = []

    async def fake_llm(message: str, agents: list[AgentDispatchInfo]) -> LlmFallbackResult:
        llm_calls.append([a.agent_id for a in agents])
        return LlmFallbackResult(agent_ids=[a.agent_id for a in agents], invoked=True)

    engine = DispatchEngine(llm_fallback=fake_llm)
    assistant = AgentDispatchInfo(
        agent_id="asst-1", name="Assistant", rules=[Rule(RuleType.ALWAYS)]
    )
    result = await engine.dispatch(
        "Of course! What task do you need assistance with?",
        [assistant],
        speaker_id="asst-1",
    )
    assert result.method is DispatchMethod.NONE
    assert result.agent_ids == []
    assert llm_calls == []


@pytest.mark.asyncio
async def test_always_teammate_does_not_bounce_on_another_agents_reply() -> None:
    engine = DispatchEngine()
    assistant = AgentDispatchInfo(
        agent_id="asst-1", name="Assistant", rules=[Rule(RuleType.ALWAYS)]
    )
    dba = AgentDispatchInfo(
        agent_id="dba-1",
        name="DBA",
        rules=[Rule(RuleType.KEYWORD, ["postgresql"], priority=10)],
    )
    # DBA just spoke; Assistant's always rule must not fire on that reply.
    skipped = await engine.dispatch("schema looks fine", [assistant, dba], speaker_id="dba-1")
    assert skipped.agent_ids == []

    # A specialist keyword on a teammate still fires (FR-5.6 beyond mentions).
    joined = await engine.dispatch(
        "we'll need a postgresql schema for this",
        [assistant, dba],
        speaker_id="asst-1",
    )
    assert joined.method is DispatchMethod.DETERMINISTIC
    assert joined.agent_ids == ["dba-1"]


@pytest.mark.asyncio
async def test_mention_can_still_retarget_the_speaker() -> None:
    engine = DispatchEngine()
    assistant = AgentDispatchInfo(
        agent_id="asst-1", name="Assistant", rules=[Rule(RuleType.ALWAYS)]
    )
    result = await engine.dispatch("cc @Assistant", [assistant], speaker_id="asst-1")
    assert result.method is DispatchMethod.MENTION
    assert result.agent_ids == ["asst-1"]
