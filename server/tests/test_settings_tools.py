"""#193: agent-facing workspace settings management -- the
get_workspace_settings/update_workspace_settings tools (tools/builtin/
settings.py) and their detection + handling in dispatch/service.py.
Mirrors test_workflow_tools.py's style: agentos.run_agent is
monkeypatched to hand back a RunOutput with the tool call already baked
in, since real tool-call detection by a live model isn't something a
fake API key can produce.
"""

from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.service import (
    _find_get_workspace_settings_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_workspace_settings_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.settings import get_workspace_settings, update_workspace_settings
from tests.conftest import authorize_agent_for_builtin_tool  # pyright: ignore[reportMissingImports]


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


# --- tool entrypoints -------------------------------------------------


def test_get_workspace_settings_tool_returns_confirmation_string() -> None:
    assert get_workspace_settings.entrypoint is not None
    assert "setting" in get_workspace_settings.entrypoint().lower()


def test_update_workspace_settings_tool_returns_confirmation_string() -> None:
    assert update_workspace_settings.entrypoint is not None
    result = update_workspace_settings.entrypoint(settings={"guard.turn_limit": 5})
    assert "guard.turn_limit" in result


# --- tool-call parsers --------------------------------------------------


def test_find_get_workspace_settings_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("get_workspace_settings", {})]
    )
    assert _find_get_workspace_settings_call(run_output) is True
    assert (
        _find_get_workspace_settings_call(RunOutput(status=RunStatus.completed, tools=None))
        is False
    )


def test_find_update_workspace_settings_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("update_workspace_settings", {"settings": {"guard.turn_limit": 5}})],
    )
    call = _find_update_workspace_settings_call(run_output)
    assert call == {"guard.turn_limit": 5}


def test_find_update_workspace_settings_call_requires_dict() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("update_workspace_settings", {"settings": "not-a-dict"})],
    )
    assert _find_update_workspace_settings_call(run_output) is None


# --- end-to-end dispatch -------------------------------------------------


def _create_agent(
    client: TestClient, headers: dict[str, str], name: str, pattern: str = "go"
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
        json={"rules": [{"rule_type": "keyword", "pattern": f'["{pattern}"]', "priority": 0}]},
        headers=headers,
    )
    return agent_id


def _create_channel_with_team(client: TestClient, headers: dict[str, str], agent_id: str) -> str:
    from tests.conftest import delete_starter_assistant

    delete_starter_assistant(client, headers)
    team = client.post(
        "/api/v1/teams", json={"name": f"Settings Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"settings-tool-test-{agent_id}"}, headers=headers
    )
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def _fake_run_agent(tool_call: ToolExecution, reply: str = "ok"):
    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[tool_call],
            get_content_as_string=lambda: reply,
        )

    return fake_run_agent


def test_get_workspace_settings_reports_current_values(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "SettingsReader")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "get_workspace_settings")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("get_workspace_settings", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go check the settings"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "guard.turn_limit" in messages[2]["content"]
    assert "10" in messages[2]["content"]


def test_update_workspace_settings_updates_known_key(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "SettingsUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workspace_settings")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("update_workspace_settings", {"settings": {"guard.turn_limit": 5}})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update the settings"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "updated workspace settings" in messages[2]["content"]

    settings = client.get("/api/v1/settings", headers=auth_headers).json()
    assert settings["guard.turn_limit"] == 5


def test_update_workspace_settings_rejects_unknown_key(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ConfusedUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workspace_settings")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("update_workspace_settings", {"settings": {"not.a.real.setting": 1}})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update the settings"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "not a known setting" in messages[2]["content"]


def test_update_workspace_settings_rejects_empty_dict(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "IndecisiveUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workspace_settings")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("update_workspace_settings", {"settings": {}})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update the settings"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "didn't specify any changes" in messages[2]["content"]


def test_update_workspace_settings_excludes_ui_port_from_sync(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "PortUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workspace_settings")

    published: list[str] = []

    async def fake_publish(_db: object, _entity_type: str, entity_id: str) -> None:
        published.append(entity_id)

    monkeypatch.setattr("rivulets.dispatch.service.publish_current_state", fake_publish)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "update_workspace_settings",
                {"settings": {"ui.port": 9000, "guard.turn_limit": 3}},
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update the settings"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers)
    assert published == ["guard.turn_limit"]
