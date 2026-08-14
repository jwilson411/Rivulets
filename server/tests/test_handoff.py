"""FR-6: the handoff tool's detection + target-invocation logic in
dispatch/service.py, and the tool function itself. agentos.run_agent is
monkeypatched — real tool-call detection by a live model isn't something
a fake API key can produce, so these tests construct the RunOutput.tools
shape directly instead (see _find_handoff_call's docstring for why that's
the boundary this module trusts agno to fill in correctly).
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.service import _find_handoff_call  # pyright: ignore[reportPrivateUsage]
from rivulets.streaming import subscribe, unsubscribe
from rivulets.tools.builtin.handoff import handoff


def test_handoff_tool_returns_confirmation_string() -> None:
    assert handoff.entrypoint is not None
    result = handoff.entrypoint(target_agent_name="DBA", context="check the schema")
    assert "DBA" in result
    assert "check the schema" in result


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


def test_find_handoff_call_extracts_target_and_context() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("handoff", {"target_agent_name": "DBA", "context": "schema help"})],
    )
    assert _find_handoff_call(run_output) == ("DBA", "schema help")


def test_find_handoff_call_ignores_other_tools() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("web_search", {"query": "something"})],
    )
    assert _find_handoff_call(run_output) is None


def test_find_handoff_call_with_no_tools() -> None:
    run_output = RunOutput(status=RunStatus.completed, tools=None)
    assert _find_handoff_call(run_output) is None


def test_find_handoff_call_ignores_malformed_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("handoff", {"target_agent_name": "DBA"})],  # missing context
    )
    assert _find_handoff_call(run_output) is None


def _create_agent(
    client: TestClient, headers: dict[str, str], name: str, rule_type: str, pattern: str = ""
) -> str:
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
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": rule_type, "pattern": pattern, "priority": 0}]},
        headers=headers,
    )
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Handoff Test Team"}, headers=headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    channel = client.post("/api/v1/channels", json={"name": "handoff-test"}, headers=headers)
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def test_handoff_posts_divider_and_invokes_target(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # keyword, not "always": DBA's reply ("Schema looks fine to me.") would
    # otherwise re-match an always-rule Architect via the recursion every
    # agent reply goes through, looping the handoff until a guard trips.
    architect_id = _create_agent(client, auth_headers, "Architect", "keyword", '["design"]')
    dba_id = _create_agent(client, auth_headers, "DBA", "mention_only")

    async def fake_run_agent(
        _db: object, agent_id: str, _message: str, *_args: object, **_kwargs: object
    ) -> Any:
        if agent_id == architect_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    _tool_execution(
                        "handoff",
                        {"target_agent_name": "DBA", "context": "Need schema review"},
                    )
                ],
                get_content_as_string=lambda: "Let me bring in the DBA.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Schema looks fine to me.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    channel_id = _create_channel_with_team(client, auth_headers, [architect_id, dba_id])
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "design the users table"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent", "system", "agent"]
    assert messages[1]["content"] == "Let me bring in the DBA."
    assert messages[2]["content_type"] == "handoff"
    assert messages[2]["content"] == "@Architect handed off to @DBA: Need schema review"
    assert messages[3]["sender_name"] == "DBA"
    assert messages[3]["content"] == "Schema looks fine to me."


def test_handoff_to_unknown_agent_is_skipped_gracefully(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # keyword, not "always" — see the comment in the test above.
    architect_id = _create_agent(client, auth_headers, "Architect", "keyword", '["start"]')

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[
                _tool_execution(
                    "handoff", {"target_agent_name": "NoSuchAgent", "context": "help please"}
                )
            ],
            get_content_as_string=lambda: "Handing this off.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    channel_id = _create_channel_with_team(client, auth_headers, [architect_id])
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "start"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["content"] == "Handing this off."


def test_handoff_publishes_sse_event(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # keyword, not "always" — see the comment in the first handoff test above.
    architect_id = _create_agent(client, auth_headers, "Architect", "keyword", '["trigger"]')
    dba_id = _create_agent(client, auth_headers, "DBA", "mention_only")

    async def fake_run_agent(
        _db: object, agent_id: str, _message: str, *_args: object, **_kwargs: object
    ) -> Any:
        if agent_id == architect_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[_tool_execution("handoff", {"target_agent_name": "DBA", "context": "ctx"})],
                get_content_as_string=lambda: "ok",
            )
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "ok"
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    channel_id = _create_channel_with_team(client, auth_headers, [architect_id, dba_id])
    # A no-match message first, just to obtain a rivulet_id before subscribing.
    first = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": "hello"}, headers=auth_headers
    )
    rivulet_id = first.json()["id"]

    event_queue = subscribe(rivulet_id, is_owner=True)
    try:
        client.post(
            f"/api/v1/rivulets/{rivulet_id}/messages",
            json={"content": "trigger again"},
            headers=auth_headers,
        )
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
    finally:
        unsubscribe(rivulet_id, event_queue)

    handoff_events = [e for e in events if e["event"] == "handoff"]
    assert len(handoff_events) == 1
    assert handoff_events[0]["data"] == {
        "from_agent_id": architect_id,
        "to_agent_name": "DBA",
        "context": "ctx",
    }
