import pytest

from rivulets.dispatch.engine import AgentDispatchInfo, DispatchEngine, DispatchMethod
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
    async def fake_llm(message: str, agents: list[AgentDispatchInfo]) -> list[str]:
        return [a.agent_id for a in agents if "overfit" in message]

    engine = DispatchEngine(llm_fallback=fake_llm)
    agent = AgentDispatchInfo(agent_id="ds-1", name="DataScientist", rules=[])
    result = await engine.dispatch(
        "I'm trying to figure out why my model keeps overfitting.", [agent]
    )
    assert result.method is DispatchMethod.LLM
    assert result.agent_ids == ["ds-1"]
