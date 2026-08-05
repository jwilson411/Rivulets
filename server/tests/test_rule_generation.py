import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.db.models import ProviderConfig, WorkspaceSetting
from agent_hive.dispatch.rule_generation import (
    GeneratedRoutingRules,
    GeneratedRule,
    _to_stored_rule,  # pyright: ignore[reportPrivateUsage]
    generate_routing_rules,
    pick_dispatcher_model,
)


def test_to_stored_rule_keyword_encodes_json_array() -> None:
    rule_type, pattern, priority = _to_stored_rule(
        GeneratedRule(rule_type="keyword", keywords=["postgres", "schema"], priority=8)
    )
    assert rule_type == "keyword"
    assert json.loads(pattern) == ["postgres", "schema"]
    assert priority == 8


def test_to_stored_rule_regex_stores_raw_string() -> None:
    rule_type, pattern, priority = _to_stored_rule(
        GeneratedRule(rule_type="regex", regex=r"(?i)\bpostgres\b", priority=6)
    )
    assert rule_type == "regex"
    assert pattern == r"(?i)\bpostgres\b"
    assert priority == 6


async def test_pick_dispatcher_model_returns_none_with_no_providers(
    db_session: AsyncSession,
) -> None:
    assert await pick_dispatcher_model(db_session) is None


async def test_pick_dispatcher_model_uses_default_provider(db_session: AsyncSession) -> None:
    db_session.add(
        ProviderConfig(provider="openai", label="OpenAI", api_key_ref="ref-1", is_default=False)
    )
    db_session.add(
        ProviderConfig(
            provider="anthropic", label="Anthropic", api_key_ref="ref-2", is_default=True
        )
    )
    await db_session.commit()

    model = await pick_dispatcher_model(db_session)
    assert model == "anthropic:claude-haiku-4-5-20251001"


async def test_pick_dispatcher_model_falls_back_to_first_when_no_default(
    db_session: AsyncSession,
) -> None:
    db_session.add(ProviderConfig(provider="deepseek", label="DeepSeek", api_key_ref="ref-1"))
    await db_session.commit()

    model = await pick_dispatcher_model(db_session)
    assert model == "deepseek:deepseek-chat"


async def test_pick_dispatcher_model_honors_workspace_override(db_session: AsyncSession) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    db_session.add(
        WorkspaceSetting(key="dispatcher.model_override", value=json.dumps("openai:o3-mini"))
    )
    await db_session.commit()

    model = await pick_dispatcher_model(db_session)
    assert model == "openai:o3-mini"


async def test_generate_routing_rules_returns_empty_with_no_provider(
    db_session: AsyncSession,
) -> None:
    rules = await generate_routing_rules(db_session, "Agent", "Does agent things", "Be helpful.")
    assert rules == []


async def test_generate_routing_rules_returns_empty_when_generator_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr("agent_hive.dispatch.rule_generation.resolve_model", fake_resolve_model)

    rules = await generate_routing_rules(db_session, "Agent", "Does agent things", "Be helpful.")
    assert rules == []


async def test_generate_routing_rules_converts_generator_output(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()  # never touched — _run_generator is mocked below too

    async def fake_run_generator(*_args: object, **_kwargs: object) -> GeneratedRoutingRules:
        return GeneratedRoutingRules(
            rules=[
                GeneratedRule(rule_type="keyword", keywords=["database", "SQL"], priority=10),
                GeneratedRule(rule_type="semantic", keywords=["help with a query"], priority=5),
            ]
        )

    monkeypatch.setattr("agent_hive.dispatch.rule_generation.resolve_model", fake_resolve_model)
    monkeypatch.setattr("agent_hive.dispatch.rule_generation._run_generator", fake_run_generator)

    rules = await generate_routing_rules(
        db_session, "DBA", "Handles database questions", "You are a DBA."
    )
    assert len(rules) == 2
    assert rules[0] == ("keyword", json.dumps(["database", "SQL"]), 10)
    assert rules[1][0] == "semantic"


def test_agent_creation_stores_generated_rules_end_to_end(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real POST /agents endpoint: only _run_generator (the
    actual LLM call) is mocked — provider setup, key storage/retrieval,
    and rule persistence all run for real."""

    async def fake_run_generator(*_args: object, **_kwargs: object) -> GeneratedRoutingRules:
        return GeneratedRoutingRules(
            rules=[GeneratedRule(rule_type="keyword", keywords=["postgres", "schema"], priority=9)]
        )

    monkeypatch.setattr("agent_hive.dispatch.rule_generation._run_generator", fake_run_generator)

    added_provider = client.post(
        "/api/v1/providers",
        json={"provider": "anthropic", "label": "Anthropic", "api_key": "sk-ant-test"},
        headers=auth_headers,
    )
    assert added_provider.status_code == 201, added_provider.text

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "DBA",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]

    rules = client.get(f"/api/v1/agents/{agent_id}/routing-rules", headers=auth_headers).json()
    assert len(rules) == 1
    assert rules[0]["rule_type"] == "keyword"
    assert json.loads(rules[0]["pattern"]) == ["postgres", "schema"]
    assert rules[0]["priority"] == 9
