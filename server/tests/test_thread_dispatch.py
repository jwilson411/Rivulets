"""End-to-end coverage of FR-4.1 (dispatch) + FR-12.1 (agent run -> message)
through the real HTTP API, with agentos.run_agent monkeypatched so no real
LLM call happens. dispatch/service.py's own selection logic (deterministic
rules, @mention, graceful skip on failure) all run for real.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient


def _fake_run_agent(content: str) -> Any:
    """A stand-in for agentos.run_agent: an async function returning
    something that duck-types agno's RunOutput just enough for
    dispatch/service.py, which checks .status and calls
    .get_content_as_string() on a successful run."""

    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: content
        )

    return fake


def _create_agent_with_always_rule(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "An agent that always responds, for testing.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]

    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "always", "pattern": "", "priority": 0}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Test Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)

    channel = client.post("/api/v1/channels", json={"name": "dispatch-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    updated = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    return channel_id


def test_keyword_rule_agent_responds_to_new_thread(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reply text deliberately doesn't contain "widget" — an "always" rule
    # would make the agent's own reply re-trigger itself (that scenario is
    # covered separately, as a turn-limit guard test); a keyword rule that
    # the *reply* doesn't match keeps this test to a single round-trip.
    monkeypatch.setattr(
        "agent_hive.dispatch.service.run_agent", _fake_run_agent("OK, doing that now.")
    )
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Keyword Agent",
            "description": "Responds to widget questions.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["sender_name"] == "Keyword Agent"
    assert messages[1]["content"] == "OK, doing that now."


def test_agent_with_no_rules_does_not_respond(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agent_hive.dispatch.service.run_agent", _fake_run_agent("should not appear")
    )
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Silent Agent",
            "description": "An agent with no routing rules configured yet.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "anything at all"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]


def test_mention_invokes_agent_regardless_of_rules(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agent_hive.dispatch.service.run_agent", _fake_run_agent("You called?"))
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "MentionOnly",
            "description": "An agent that only responds when @mentioned.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "mention_only", "pattern": "", "priority": 0}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "hey @MentionOnly can you help?"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["content"] == "You called?"


def test_agent_run_failure_is_skipped_gracefully(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-2.4: a failing agent run must not 500 the request or block the
    human message from being persisted."""

    def _boom(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("agent_hive.dispatch.service.run_agent", _boom)
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Flaky Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "anything at all"},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]


def test_run_status_error_posts_system_alert_not_raw_error_text(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: found via a real end-to-end run with a bad API key.
    agno doesn't raise on a provider HTTP error — it returns a normal
    RunOutput with status=ERROR and the raw error string as `content`.
    Without checking `.status`, that error text got persisted as if the
    agent had said it."""

    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.error,
            content="Error code: 401 - invalid x-api-key",
            get_content_as_string=lambda: "Error code: 401 - invalid x-api-key",
        )

    monkeypatch.setattr("agent_hive.dispatch.service.run_agent", fake)
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Broken Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "anything at all"},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "system"]
    assert messages[1]["content_type"] == "system_alert"
    assert "401" not in messages[1]["content"]
    assert "Broken Agent" in messages[1]["content"]


def test_channel_with_no_team_gets_no_dispatch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel = client.post("/api/v1/channels", json={"name": "no-team"}, headers=auth_headers)
    channel_id = channel.json()["id"]

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "hello"},
        headers=auth_headers,
    )
    assert thread.status_code == 201
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]
