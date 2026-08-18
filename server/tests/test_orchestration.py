"""Assistant-as-orchestrator: specialists speak on handoff / @mention,
and Assistant stays on every channel."""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.engine import AgentDispatchInfo, DispatchMethod, DispatchResult
from rivulets.dispatch.orchestration import apply_orchestrator_lock, format_team_roster


def test_lock_routes_human_turns_to_orchestrator_only() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder", "assistant"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=False,
        from_agent_id=None,
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["assistant"]
    assert result.method is DispatchMethod.DEFAULT


def test_lock_drops_unsolicited_agent_rematch() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=False,
        from_agent_id="assistant",
        orchestrator_id="assistant",
    )
    assert result.agent_ids == []


def test_lock_preserves_mentions() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder"], method=DispatchMethod.MENTION),
        team_engaged=False,
        from_agent_id=None,
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["coder"]


def test_lock_stays_on_after_engage() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=True,
        from_agent_id=None,
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["assistant"]
    assert result.method is DispatchMethod.DEFAULT


def test_lock_is_noop_without_orchestrator() -> None:
    original = DispatchResult(agent_ids=["coder"], method=DispatchMethod.DETERMINISTIC)
    assert (
        apply_orchestrator_lock(
            original, team_engaged=False, from_agent_id=None, orchestrator_id=None
        )
        is original
    )


def test_format_team_roster_lists_specialists_not_assistant() -> None:
    assistant = SimpleNamespace(name="Assistant", description="orchestrator")
    coder = SimpleNamespace(name="Coder", description="Writes code.")
    unnamed = SimpleNamespace(name="Scout", description="")
    roster = format_team_roster(
        [
            (assistant, AgentDispatchInfo(agent_id="a", name="Assistant")),
            (coder, AgentDispatchInfo(agent_id="c", name="Coder")),
            (unnamed, AgentDispatchInfo(agent_id="s", name="Scout")),
        ]
    )
    assert "Coder: Writes code." in roster
    assert "- Scout" in roster
    assert "Assistant" not in roster.split("\n", 1)[1]


def test_lock_returns_specialist_reply_to_orchestrator() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["writer"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=True,
        from_agent_id="coder",
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["assistant"]
    assert result.method is DispatchMethod.DEFAULT


def _fake_run_agent(content: str) -> Any:
    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: content
        )

    return fake


def _create_keyword_agent(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": f"Test agent {name}",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    agent_id: str = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=headers,
    )
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str], name: str = "orch-test"
) -> str:
    team = client.post("/api/v1/teams", json={"name": f"{name} team"}, headers=headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    channel = client.post("/api/v1/channels", json={"name": name}, headers=headers)
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def test_locked_human_message_reaches_only_assistant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, message: str, **_kwargs: object) -> Any:
        seen.append(message)
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Which widget?",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    channel_id = _create_channel_with_team(client, auth_headers, [coder])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    agent_names = [m["sender_name"] for m in messages if m["sender_type"] == "agent"]
    assert agent_names == ["Assistant"]
    assert all(m["sender_name"] != "WidgetCoder" for m in messages)


def test_mention_bypasses_lock(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent", _fake_run_agent("WidgetCoder here.")
    )
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="mention-lock")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@WidgetCoder tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["sender_name"] == "WidgetCoder" for m in messages)


def test_human_engage_nudges_assistant_not_keyword_specialists(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_run_agent(_db: object, _agent_id: str, message: str, **_kwargs: object) -> Any:
        seen.append(message)
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Picking someone.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="engage-lock")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    before = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert all(m["sender_name"] != "WidgetCoder" for m in before)

    engaged = client.post(
        f"/api/v1/rivulets/{rivulet_id}/engage-team",
        json={"reason": "Enough context"},
        headers=auth_headers,
    )
    assert engaged.status_code == 201, engaged.text
    assert engaged.json()["content_type"] == "team_engaged"

    after = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["content_type"] == "team_engaged" for m in after)
    assert all(m["sender_name"] != "WidgetCoder" for m in after)
    assert any(m["sender_name"] == "Assistant" for m in after)
    assert any("pick the right teammate" in message for message in seen)


def test_assistant_engage_team_tool_does_not_unlock_keywords(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    ToolExecution(
                        tool_name="engage_team", tool_args={"reason": "Need the coder"}
                    )
                ],
                get_content_as_string=lambda: "Bringing the team in.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Widget details follow.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="tool-engage")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["content_type"] == "team_engaged" for m in messages)
    assert all(m["sender_name"] != "WidgetCoder" for m in messages)


def test_assistant_answers_a_channel_with_no_team(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent("Here."))
    channel = client.post("/api/v1/channels", json={"name": "no-team-orch"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hello"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["sender_name"] == "Assistant"


def test_history_is_passed_to_the_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_run_agent(_db: object, _agent_id: str, message: str, **_kwargs: object) -> Any:
        seen.append(message)
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "ok"
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel = client.post("/api/v1/channels", json={"name": "hist-prompt"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    first = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "remember the color blue"},
        headers=auth_headers,
    )
    rivulet_id = first.json()["id"]
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "what color was that?"},
        headers=auth_headers,
    )
    assert len(seen) >= 2
    assert "remember the color blue" in seen[-1]
    assert "[Conversation so far]" in seen[-1]
    assert "[Current message]" in seen[-1]
    assert "what color was that?" in seen[-1]
    assert "[Channel team]" in seen[-1]


def test_handoff_invokes_specialist_and_reply_returns_to_assistant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    writer = _create_keyword_agent(client, auth_headers, "WidgetWriter")
    invoked: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        invoked.append(agent_id)
        if agent_id == assistant_id and invoked.count(assistant_id) == 1:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    ToolExecution(
                        tool_name="handoff",
                        tool_args={
                            "target_agent_name": "WidgetCoder",
                            "context": "Explain the widget",
                        },
                    )
                ],
                get_content_as_string=lambda: "Handing to the coder.",
            )
        if agent_id == coder:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=None,
                get_content_as_string=lambda: "The widget is a code thing.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Noted.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(
        client, auth_headers, [coder, writer], name="handoff-route"
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    names = [m["sender_name"] for m in messages if m["sender_type"] == "agent"]
    assert names[0] == "Assistant"
    assert "WidgetCoder" in names
    assert "WidgetWriter" not in names
    assert names[-1] == "Assistant"
    assert invoked.count(writer) == 0


def test_hire_teammate_then_handoff_invokes_new_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    ToolExecution(
                        tool_name="hire_teammate",
                        tool_args={
                            "name": "DBA",
                            "role": "Reviews schemas and writes SQL.",
                            "instructions": "You are a database administrator.",
                            "assignment": "Check the users table.",
                        },
                    ),
                    ToolExecution(
                        tool_name="handoff",
                        tool_args={
                            "target_agent_name": "DBA",
                            "context": "Check the users table.",
                        },
                    ),
                ],
                get_content_as_string=lambda: "Hiring a DBA.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Schema looks fine.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="hire-dba")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "we need a database review"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any("hired @DBA" in (m["content"] or "") for m in messages)
    assert any(m["content_type"] == "handoff" for m in messages)
    assert any(m["sender_name"] == "DBA" for m in messages)
    created = client.get("/api/v1/agents", headers=auth_headers).json()
    assert any(a["name"] == "DBA" for a in created)


def test_non_orchestrator_hire_is_ignored(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "HireCoder")

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=None,
                get_content_as_string=lambda: "Noted.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[
                ToolExecution(
                    tool_name="hire_teammate",
                    tool_args={
                        "name": "SneakyDBA",
                        "role": "Should not be created.",
                        "instructions": "Nope.",
                        "assignment": "Nope.",
                    },
                )
            ],
            get_content_as_string=lambda: "Trying to hire.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="sneaky-hire")
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@HireCoder hire someone"},
        headers=auth_headers,
    )
    created = client.get("/api/v1/agents", headers=auth_headers).json()
    assert all(a["name"] != "SneakyDBA" for a in created)
