"""Workspace-level usage/cost dashboard (api/usage.py, #28) — aggregation
across AgentRun rows populated by dispatch/service.py's `_record_agent_run`.
Mirrors test_rivulet_dispatch.py's pattern of monkeypatching
`rivulets.dispatch.service.run_agent` so no real LLM call happens.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from rivulets.db.models import AgentRun
from rivulets.db.session import session_scope


def _fake_run_agent_with_metrics(content: str, input_tokens: int, output_tokens: int) -> Any:
    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: content,
            metrics=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    return fake


def _create_agent_with_keyword_rule(
    client: TestClient, headers: dict[str, str], name: str, model: str
) -> str:
    """A keyword rule (not "always") so the agent's own reply — deliberately
    kept free of the keyword by every fake reply text below — doesn't
    re-trigger it via dispatch/service.py's recursion (FR-5.6). "always"
    would make each test's single triggering message fan out into several
    AgentRun rows instead of exactly one, per test_rivulet_dispatch.py's
    own comment on the same tradeoff."""
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "Responds to widget questions.",
            "instructions": "Say OK.",
            "model": model,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Usage Test Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)

    channel = client.post("/api/v1/channels", json={"name": "usage-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    updated = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    return channel_id


def test_usage_empty_workspace(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/usage", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_count"] == 0
    assert body["total_tokens"] == 0
    assert body["total_cost_usd"] == 0
    assert body["cost_incomplete"] is False
    assert body["by_agent"] == []
    assert body["by_model"] == []


def test_usage_aggregates_after_dispatch(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent_with_metrics("OK, doing that now.", input_tokens=1000, output_tokens=500),
    )
    agent_id = _create_agent_with_keyword_rule(
        client, auth_headers, "Usage Agent", "anthropic:claude-3-5-haiku-latest"
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text

    usage = client.get("/api/v1/usage", headers=auth_headers)
    assert usage.status_code == 200, usage.text
    body = usage.json()

    assert body["run_count"] == 1
    assert body["total_input_tokens"] == 1000
    assert body["total_output_tokens"] == 500
    assert body["total_tokens"] == 1500
    assert body["cost_incomplete"] is False
    # claude-3-5-haiku-latest: $0.80/$4.00 per 1M -> 1000*0.80e-6 + 500*4.00e-6
    assert body["total_cost_usd"] == pytest.approx(0.0028)

    assert len(body["by_agent"]) == 1
    assert body["by_agent"][0]["agent_id"] == agent_id
    assert body["by_agent"][0]["agent_name"] == "Usage Agent"
    assert body["by_agent"][0]["total_tokens"] == 1500
    assert body["by_agent"][0]["cost_usd"] == pytest.approx(0.0028)

    assert len(body["by_model"]) == 1
    assert body["by_model"][0]["model"] == "anthropic:claude-3-5-haiku-latest"
    assert body["by_model"][0]["tier"] is None  # fixed-model agent, not Auto mode
    assert body["by_model"][0]["total_tokens"] == 1500

    # FR-3.5's per-agent view reads the same AgentRun rows.
    runs = client.get(f"/api/v1/agents/{agent_id}/runs", headers=auth_headers)
    assert runs.status_code == 200, runs.text
    run_list = runs.json()
    assert len(run_list) == 1
    assert run_list[0]["status"] == "completed"
    assert run_list[0]["total_tokens"] == 1500


def test_usage_unpriced_model_flags_cost_incomplete(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent_with_metrics("OK.", input_tokens=100, output_tokens=50),
    )
    agent_id = _create_agent_with_keyword_rule(
        client, auth_headers, "Unpriced Agent", "openai_compatible:local-llama"
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )

    body = client.get("/api/v1/usage", headers=auth_headers).json()
    assert body["run_count"] == 1
    assert body["total_tokens"] == 150
    assert body["cost_incomplete"] is True
    assert body["total_cost_usd"] == 0
    assert body["by_agent"][0]["cost_usd"] is None


async def test_usage_range_excludes_older_runs(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent_with_metrics("OK.", input_tokens=10, output_tokens=10),
    )
    agent_id = _create_agent_with_keyword_rule(
        client, auth_headers, "Old Agent", "anthropic:claude-3-5-haiku-latest"
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )

    # Backdate the run this dispatch just created well outside every window.
    async with session_scope() as session:
        run = (await session.execute(select(AgentRun))).scalars().first()
        assert run is not None
        await session.execute(
            update(AgentRun).where(AgentRun.id == run.id).values(created_at="2000-01-01T00:00:00Z")
        )
        await session.commit()

    for range_ in ("day", "week", "month"):
        body = client.get(f"/api/v1/usage?range={range_}", headers=auth_headers).json()
        assert body["run_count"] == 0, f"range={range_}"


async def test_usage_counts_dispatcher_call_rows_in_totals_but_not_by_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#246: llm_fallback.py's routing call records an AgentRun with
    agent_id=None, source='dispatcher_call' -- it must still count toward
    workspace totals/by_model (an outerjoin, not an inner join), but has
    nothing to key a by_agent bucket under."""
    async with session_scope() as session:
        session.add(
            AgentRun(
                agent_id=None,
                source="dispatcher_call",
                model="anthropic:claude-3-5-haiku-latest",
                status="completed",
                input_tokens=200,
                output_tokens=100,
                total_tokens=300,
                cost_usd=0.0005,
            )
        )
        await session.commit()

    body = client.get("/api/v1/usage", headers=auth_headers).json()
    assert body["run_count"] == 1
    assert body["total_tokens"] == 300
    assert body["total_cost_usd"] == pytest.approx(0.0005)
    assert body["by_agent"] == []
    assert len(body["by_model"]) == 1
    assert body["by_model"][0]["model"] == "anthropic:claude-3-5-haiku-latest"
    assert body["by_model"][0]["total_tokens"] == 300


async def test_usage_collapses_same_model_across_tiers(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#419: Auto cheap + a fixed-model (or capable) run of the same
    provider:id must be one by_model row, not two identical labels."""
    async with session_scope() as session:
        session.add(
            AgentRun(
                agent_id=None,
                source="dispatcher_call",
                model="openai:gpt-4o-mini",
                tier="cheap",
                status="completed",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cost_usd=0.0001,
            )
        )
        session.add(
            AgentRun(
                agent_id=None,
                source="dispatcher_call",
                model="openai:gpt-4o-mini",
                tier=None,
                status="completed",
                input_tokens=1000,
                output_tokens=500,
                total_tokens=1500,
                cost_usd=0.0003,
            )
        )
        await session.commit()

    body = client.get("/api/v1/usage", headers=auth_headers).json()
    assert body["run_count"] == 2
    assert body["total_tokens"] == 1515
    assert len(body["by_model"]) == 1
    assert body["by_model"][0]["model"] == "openai:gpt-4o-mini"
    assert body["by_model"][0]["total_tokens"] == 1515
    assert body["by_model"][0]["run_count"] == 2
    # Mixed tiers collapse to no single-tier label.
    assert body["by_model"][0]["tier"] is None
