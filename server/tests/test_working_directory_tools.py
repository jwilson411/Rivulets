"""set_working_directory tool — rivulet override only, never the river."""

from pathlib import Path
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.service import (
    _find_set_working_directory_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.sync.apply import CHANNEL_SPEC, RIVULET_SPEC
from rivulets.tools.builtin.filesystem import set_working_directory
from tests.conftest import authorize_agent_for_builtin_tool  # pyright: ignore[reportMissingImports]


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


def test_set_working_directory_tool_returns_confirmation(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    assert set_working_directory.entrypoint is not None
    result = set_working_directory.entrypoint(path=str(project))
    assert str(project.resolve()) in result


def test_find_set_working_directory_call_extracts_path() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("set_working_directory", {"path": "/Users/ada/src"})],
    )
    assert _find_set_working_directory_call(run_output) == "/Users/ada/src"


def test_working_directory_is_not_synced() -> None:
    assert "working_directory" not in CHANNEL_SPEC.synced_fields
    assert "working_directory" not in RIVULET_SPEC.synced_fields


def _create_agent(client: TestClient, headers: dict[str, str], name: str) -> str:
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
        json={"rules": [{"rule_type": "keyword", "pattern": '["go"]', "priority": 0}]},
        headers=headers,
    )
    return agent_id


def _create_channel_with_team(client: TestClient, headers: dict[str, str], agent_id: str) -> str:
    from tests.conftest import delete_starter_assistant

    delete_starter_assistant(client, headers)
    team = client.post(
        "/api/v1/teams", json={"name": f"WD Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"wd-tool-test-{agent_id}"}, headers=headers
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


def test_set_working_directory_updates_rivulet_not_channel(
    client: TestClient,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    river = tmp_path / "river"
    stream = tmp_path / "stream"
    river.mkdir()
    stream.mkdir()
    agent_id = _create_agent(client, auth_headers, "FolderSetter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    client.patch(
        f"/api/v1/channels/{channel_id}",
        json={"working_directory": str(river)},
        headers=auth_headers,
    )
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "set_working_directory")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("set_working_directory", {"path": str(stream)})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go set the folder"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    loaded = client.get(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers).json()
    assert loaded["working_directory"] == str(stream.resolve())
    channel = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert channel["working_directory"] == str(river.resolve())
