"""Unit tests for the broadcaster (streaming.py) plus an integration test
confirming dispatch/service.py publishes the documented SSE event
sequence (api-design.md#sse-protocol) for a streaming agent run.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.streaming import publish, subscribe, unsubscribe


def test_publish_with_no_subscribers_does_not_raise() -> None:
    publish("no-such-rivulet", "done", {"rivulet_id": "no-such-rivulet"})


def test_subscriber_receives_published_event() -> None:
    queue = subscribe("rivulet-1")
    try:
        publish("rivulet-1", "agent_token", {"token": "hi"})
        event = queue.get_nowait()
        assert event == {"event": "agent_token", "data": {"token": "hi"}}
    finally:
        unsubscribe("rivulet-1", queue)


def test_multiple_subscribers_all_receive_the_same_event() -> None:
    queue_a = subscribe("rivulet-2")
    queue_b = subscribe("rivulet-2")
    try:
        publish("rivulet-2", "done", {"rivulet_id": "rivulet-2"})
        assert queue_a.get_nowait()["event"] == "done"
        assert queue_b.get_nowait()["event"] == "done"
    finally:
        unsubscribe("rivulet-2", queue_a)
        unsubscribe("rivulet-2", queue_b)


def test_unsubscribed_queue_receives_nothing_further() -> None:
    queue = subscribe("rivulet-3")
    unsubscribe("rivulet-3", queue)
    publish("rivulet-3", "done", {"rivulet_id": "rivulet-3"})
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


def test_unsubscribe_is_idempotent() -> None:
    queue = subscribe("rivulet-4")
    unsubscribe("rivulet-4", queue)
    unsubscribe("rivulet-4", queue)  # must not raise


def test_dispatch_publishes_documented_sse_event_sequence(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_streaming_run_agent(
        _db: object,
        _agent_id: str,
        _message: str,
        session_id: str,  # noqa: ARG001
        user_id: str = "human",  # noqa: ARG001
        on_token: Any = None,
        model_override: Any = None,  # noqa: ARG001
    ) -> Any:
        if on_token is not None:
            on_token("Hel")
            on_token("lo")
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "Hello"
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_streaming_run_agent)

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Streamer",
            "description": "Responds to trigger messages.",
            "instructions": "Say hi.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["trigger"]', "priority": 10}]},
        headers=auth_headers,
    )
    team = client.post("/api/v1/teams", json={"name": "T"}, headers=auth_headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=auth_headers)
    channel = client.post("/api/v1/channels", json={"name": "stream-test"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=auth_headers)

    # First message doesn't match the keyword rule — just here to get a
    # rivulet_id to subscribe to before the message that actually dispatches.
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "no match here"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    event_queue = subscribe(rivulet_id)
    try:
        response = client.post(
            f"/api/v1/rivulets/{rivulet_id}/messages",
            json={"content": "trigger now"},
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
    finally:
        unsubscribe(rivulet_id, event_queue)

    assert [e["event"] for e in events] == ["agent_token", "agent_token", "agent_message", "done"]
    assert [e["data"]["token"] for e in events[:2]] == ["Hel", "lo"]
    assert events[2]["data"]["content"] == "Hello"
    assert events[2]["data"]["agent_id"] == agent_id
    assert events[3]["data"] == {"rivulet_id": rivulet_id}
