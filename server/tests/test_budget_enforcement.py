"""Budget cap enforcement (dispatch/budgets.py wired into
dispatch/service.py's _invoke_agent, #97) through the real HTTP API, with
agentos.run_agent mocked so no real LLM call happens -- same pattern
test_guards.py and test_usage_api.py already use.

Each fake run costs exactly $1.00 (claude-3-5-haiku-latest: $4.00/1M output
tokens * 250_000 output tokens, 0 input tokens -- see agentos/pricing.py),
so a $0.50 cap is never breached by the run that trips it (the check runs
*before* that call, per #97's "in-flight work finishes" decision) but is
always breached by the next one.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from rivulets.db.models import AgentRun, BudgetCapState
from rivulets.db.session import session_scope

_ONE_DOLLAR_RUN = SimpleNamespace(
    input_tokens=0, output_tokens=250_000
)  # 250_000 * $4.00/1M = $1.00


def _fake_run_agent(*_args: object, **_kwargs: object) -> Any:
    return SimpleNamespace(
        status=RunStatus.completed,
        tools=None,
        get_content_as_string=lambda: "OK.",
        metrics=SimpleNamespace(
            input_tokens=_ONE_DOLLAR_RUN.input_tokens,
            output_tokens=_ONE_DOLLAR_RUN.output_tokens,
            total_tokens=_ONE_DOLLAR_RUN.input_tokens + _ONE_DOLLAR_RUN.output_tokens,
        ),
    )


def _counting_fake_run_agent(calls: list[str]) -> Any:
    async def fake(_db: object, agent_id: str, *_args: object, **_kwargs: object) -> Any:
        calls.append(agent_id)
        return _fake_run_agent()

    return fake


def _create_agent_with_keyword_rule(
    client: TestClient, headers: dict[str, str], name: str, keyword: str
) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "Test agent",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": f'["{keyword}"]', "priority": 0}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Budget Test Team"}, headers=headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    channel = client.post("/api/v1/channels", json={"name": "budget-test"}, headers=headers)
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def _create_cap(
    client: TestClient,
    headers: dict[str, str],
    *,
    scope_type: str,
    action: str,
    limit_usd: float = 0.5,
    agent_id: str | None = None,
    team_id: str | None = None,
    period: str = "day",
) -> str:
    body = {
        "scope_type": scope_type,
        "period": period,
        "limit_usd": limit_usd,
        "action": action,
    }
    if agent_id is not None:
        body["agent_id"] = agent_id
    if team_id is not None:
        body["team_id"] = team_id
    created = client.post("/api/v1/budgets", json=body, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_alert_cap_posts_one_system_alert_per_period_not_per_turn(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Spendy", "widget")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _create_cap(client, auth_headers, scope_type="agent", action="alert", agent_id=agent_id)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    # First run: pre-run spend is $0, cap not yet breached -- goes through.
    assert len(calls) == 1

    for _ in range(3):
        client.post(
            f"/api/v1/rivulets/{rivulet_id}/messages",
            json={"content": "another widget question"},
            headers=auth_headers,
        )
    # Every subsequent turn: pre-run spend is >= $1.00, over the $0.50 cap
    # -- alert-only, so the model still runs each time.
    assert len(calls) == 4

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    budget_alerts = [
        m
        for m in messages
        if m["content_type"] == "system_alert" and "budget cap" in m["content"].lower()
    ]
    # Breached on every turn after the first, but alerted only once per
    # period -- dedup via BudgetCapState.alerted_at.
    assert len(budget_alerts) == 1


def test_hard_stop_cap_blocks_new_calls_without_touching_in_flight_run(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Spendy", "widget")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _create_cap(client, auth_headers, scope_type="agent", action="hard_stop", agent_id=agent_id)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    # #97 decision: in-flight work always finishes -- the run that pushes
    # spend over the limit is never itself blocked.
    assert len(calls) == 1

    blocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another widget question"},
        headers=auth_headers,
    )
    assert blocked.status_code == 201, blocked.text
    # Only the *next* call is refused -- the model is never invoked again.
    assert len(calls) == 1

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert messages[-1]["content_type"] == "system_alert"
    assert "blocked" in messages[-1]["content"].lower()

    # Blocked repeatedly, still never re-invoking the model.
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "yet another widget question"},
        headers=auth_headers,
    )
    assert len(calls) == 1


def test_team_scope_cap_blocks_a_different_agent_on_the_same_team(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_a = _create_agent_with_keyword_rule(client, auth_headers, "AgentA", "alpha")
    agent_b = _create_agent_with_keyword_rule(client, auth_headers, "AgentB", "beta")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_a, agent_b])
    team = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    team_id = team["team_id"]
    _create_cap(client, auth_headers, scope_type="team", action="hard_stop", team_id=team_id)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": "alpha"}, headers=auth_headers
    )
    rivulet_id = rivulet.json()["id"]
    assert calls == [agent_a]

    # AgentA's own $1.00 already exceeds the team's $0.50 cap, so AgentB
    # (same team, never having spent anything itself) is blocked too.
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages", json={"content": "beta"}, headers=auth_headers
    )
    assert calls == [agent_a]  # AgentB's model was never invoked

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert messages[-1]["content_type"] == "system_alert"


def test_workspace_scope_cap_blocks_any_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Spendy", "widget")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _create_cap(client, auth_headers, scope_type="workspace", action="hard_stop")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    assert len(calls) == 1

    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another widget question"},
        headers=auth_headers,
    )
    assert len(calls) == 1


def test_unpriced_runs_excluded_from_spend_but_counted_separately(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_unpriced(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "OK.",
            metrics=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_unpriced)

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Unpriced Agent",
            "description": "Test agent",
            "instructions": "Say OK.",
            "model": "openai_compatible:local-llama",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 0}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    cap_id = _create_cap(
        client,
        auth_headers,
        scope_type="agent",
        action="hard_stop",
        limit_usd=0.01,
        agent_id=agent_id,
    )

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another widget question"},
        headers=auth_headers,
    )

    status_body = client.get(f"/api/v1/budgets/{cap_id}/status", headers=auth_headers).json()
    assert status_body["spend_usd"] == 0
    assert status_body["unpriced_run_count"] == 2
    assert status_body["breached"] is False
    assert status_body["blocked"] is False


async def test_period_rollover_unblocks_without_override(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Spendy", "widget")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _create_cap(client, auth_headers, scope_type="agent", action="hard_stop", agent_id=agent_id)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    assert len(calls) == 1

    blocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another widget question"},
        headers=auth_headers,
    )
    assert blocked.status_code == 201
    assert len(calls) == 1

    # Move the existing run's spend outside the current 'day' window --
    # same backdating technique test_usage_range_excludes_older_runs uses.
    async with session_scope() as session:
        run = (await session.execute(select(AgentRun))).scalars().first()
        assert run is not None
        await session.execute(
            update(AgentRun).where(AgentRun.id == run.id).values(created_at="2000-01-01T00:00:00Z")
        )
        await session.commit()

    unblocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "yet another widget question"},
        headers=auth_headers,
    )
    assert unblocked.status_code == 201, unblocked.text
    # No override call was made -- the window rolling over was enough.
    assert len(calls) == 2


async def test_override_unblocks_current_period_but_ignores_a_stale_one(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Spendy", "widget")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    cap_id = _create_cap(
        client, auth_headers, scope_type="agent", action="hard_stop", agent_id=agent_id
    )

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another widget question"},
        headers=auth_headers,
    )
    assert len(calls) == 1  # second call already blocked

    # Simulate an override left over from a previous period: it must NOT
    # count toward the *current* period_start (BudgetCapState.docstring's
    # auto-expiry invariant).
    async with session_scope() as session:
        state = await session.get(BudgetCapState, cap_id)
        assert state is not None
        state.override_active = True
        state.override_period_start = "2000-01-01T00:00:00Z"
        await session.commit()

    still_blocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "still asking about the widget"},
        headers=auth_headers,
    )
    assert still_blocked.status_code == 201
    assert len(calls) == 1  # stale override ignored, still blocked

    # A real override against the current period unblocks it.
    overridden = client.post(f"/api/v1/budgets/{cap_id}/override", headers=auth_headers)
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["blocked"] is False

    unblocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "widget again"},
        headers=auth_headers,
    )
    assert unblocked.status_code == 201
    assert len(calls) == 2
