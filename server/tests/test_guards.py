"""Loop prevention guards (FR-7) through the real HTTP API, with
agentos.run_agent mocked so no real LLM call happens. Exercises the
agent-to-agent recursion dispatch/service.py added specifically to give
these guards something to bound (see its module docstring).
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient


def _create_agent(client: TestClient, headers: dict[str, str], name: str, rule_type: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": f"Test agent {name}",
            "instructions": "Say something.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": rule_type, "pattern": "", "priority": 0}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Guard Test Team"}, headers=headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    channel = client.post("/api/v1/channels", json={"name": "guard-test"}, headers=headers)
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def test_turn_limit_pauses_a_self_triggering_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An 'always' agent replying to its own output is exactly the
    scenario FR-7.1 exists for: with nothing to stop it, one human message
    would recurse forever.

    A lone self-looping agent produces the same (Loopy, Loopy) pair every
    time, so cycle detection (default threshold 3) would otherwise trip
    first at message 4 — cycle_threshold is pushed out of reach here so
    this test isolates the turn limit specifically.
    """

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "OK."
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    settings = client.patch(
        "/api/v1/settings", json={"guard.cycle_threshold": 20}, headers=auth_headers
    )
    assert settings.status_code == 200, settings.text

    agent_id = _create_agent(client, auth_headers, "Loopy", "always")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "start"},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    # Default guard.turn_limit is 10: 1 human + 10 agent replies, then the
    # 10th one trips the limit and appends a system pause message.
    assert len(messages) == 12
    assert [m["sender_type"] for m in messages[:11]] == ["human"] + ["agent"] * 10
    assert messages[11]["sender_type"] == "system"
    assert messages[11]["content_type"] == "system_alert"
    assert "turn limit" in messages[11]["content"].lower()

    thread_state = client.get(f"/api/v1/threads/{thread_id}", headers=auth_headers).json()
    assert thread_state["status"] == "paused"


def test_paused_thread_resumes_and_dispatches_again(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-7.5: resuming resets counters and re-enables dispatch. Uses the
    same self-looping agent as the cycle-detection test above (default
    thresholds, no override) — it'll pause again quickly, but the point
    here is just that it responds *at all* after being resumed, proving
    reset_guard_state() actually ran rather than leaving `paused` stuck."""

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "OK."
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    agent_id = _create_agent(client, auth_headers, "Loopy", "always")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads", json={"content": "start"}, headers=auth_headers
    )
    thread_id = thread.json()["id"]
    before = len(client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json())

    resumed = client.post(f"/api/v1/threads/{thread_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    client.post(
        f"/api/v1/threads/{thread_id}/messages", json={"content": "continue"}, headers=auth_headers
    )
    after = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    new_messages = after[before:]
    assert len(new_messages) > 0
    assert any(m["sender_type"] == "agent" for m in new_messages)


def test_cycle_detection_pauses_two_agents_mentioning_each_other(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_a = _create_agent(client, auth_headers, "AgentA", "mention_only")
    agent_b = _create_agent(client, auth_headers, "AgentB", "mention_only")

    async def fake_run_agent(
        _db: object, agent_id: str, _message: str, *_args: object, **_kwargs: object
    ) -> Any:
        content = "ping @AgentB" if agent_id == agent_a else "ping @AgentA"
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: content
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    channel_id = _create_channel_with_team(client, auth_headers, [agent_a, agent_b])
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "@AgentA kick things off"},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    # A->B->A->B->A->B: the (A,B) pair recurs a 3rd time on the 6th agent
    # message, tripping guard.cycle_threshold's default of 3.
    assert len(messages) == 8
    assert [m["sender_type"] for m in messages[:7]] == ["human"] + ["agent"] * 6
    assert messages[7]["sender_type"] == "system"
    assert "loop" in messages[7]["content"].lower()

    thread_state = client.get(f"/api/v1/threads/{thread_id}", headers=auth_headers).json()
    assert thread_state["status"] == "paused"


def test_timeout_pauses_when_configured_to_zero_minutes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "OK."
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    settings = client.patch(
        "/api/v1/settings", json={"guard.timeout_minutes": 0}, headers=auth_headers
    )
    assert settings.status_code == 200, settings.text

    agent_id = _create_agent(client, auth_headers, "Slowpoke", "always")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "start"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    # With a 0-minute allowance, any elapsed wall-clock time trips it — the
    # very first agent reply's guard check already exceeds it.
    assert messages[-1]["sender_type"] == "system"
    assert "minutes" in messages[-1]["content"].lower()
