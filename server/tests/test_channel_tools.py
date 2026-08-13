"""#189: agent-facing channel management -- the create_channel/
update_channel/archive_channel/unarchive_channel/reorder_channels/
list_channels tools (tools/builtin/channels.py) and their detection +
handling in dispatch/service.py. Mirrors test_schedule_tools.py's style:
agentos.run_agent is monkeypatched to hand back a RunOutput with the tool
call already baked in, since real tool-call detection by a live model
isn't something a fake API key can produce.
"""

from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.service import (
    _find_archive_channel_call,  # pyright: ignore[reportPrivateUsage]
    _find_create_channel_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_channels_call,  # pyright: ignore[reportPrivateUsage]
    _find_reorder_channels_call,  # pyright: ignore[reportPrivateUsage]
    _find_unarchive_channel_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_channel_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.channels import (
    archive_channel,
    create_channel,
    list_channels,
    reorder_channels,
    unarchive_channel,
    update_channel,
)
from tests.conftest import authorize_agent_for_builtin_tool


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


# --- tool entrypoints -------------------------------------------------


def test_create_channel_tool_returns_confirmation_string() -> None:
    assert create_channel.entrypoint is not None
    assert "roadmap" in create_channel.entrypoint(name="roadmap")


def test_update_channel_tool_returns_confirmation_string() -> None:
    assert update_channel.entrypoint is not None
    assert "roadmap" in update_channel.entrypoint(channel="roadmap", name="roadmap-2026")


def test_archive_channel_tool_returns_confirmation_string() -> None:
    assert archive_channel.entrypoint is not None
    assert "roadmap" in archive_channel.entrypoint(channel="roadmap")


def test_unarchive_channel_tool_returns_confirmation_string() -> None:
    assert unarchive_channel.entrypoint is not None
    assert "roadmap" in unarchive_channel.entrypoint(channel="roadmap")


def test_reorder_channels_tool_returns_confirmation_string() -> None:
    assert reorder_channels.entrypoint is not None
    assert "2" in reorder_channels.entrypoint(order=["a", "b"])


def test_list_channels_tool_returns_confirmation_string() -> None:
    assert list_channels.entrypoint is not None
    assert "channel" in list_channels.entrypoint().lower()


# --- tool-call parsers --------------------------------------------------


def test_find_create_channel_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("create_channel", {"name": "roadmap", "description": "Planning"})],
    )
    call = _find_create_channel_call(run_output)
    assert call is not None
    assert call.name == "roadmap"
    assert call.description == "Planning"


def test_find_create_channel_call_missing_name_returns_none() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("create_channel", {})]
    )
    assert _find_create_channel_call(run_output) is None


def test_find_update_channel_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("update_channel", {"channel": "roadmap", "name": "roadmap-2026"})],
    )
    call = _find_update_channel_call(run_output)
    assert call is not None
    assert call.channel_ref == "roadmap"
    assert call.name == "roadmap-2026"
    assert call.description is None


def test_find_archive_channel_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("archive_channel", {"channel": "roadmap"})],
    )
    assert _find_archive_channel_call(run_output) == "roadmap"


def test_find_unarchive_channel_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("unarchive_channel", {"channel": "roadmap"})],
    )
    assert _find_unarchive_channel_call(run_output) == "roadmap"


def test_find_reorder_channels_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("reorder_channels", {"order": ["b", "a"]})],
    )
    assert _find_reorder_channels_call(run_output) == ["b", "a"]


def test_find_reorder_channels_call_non_string_items_returns_none() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("reorder_channels", {"order": ["a", 2]})],
    )
    assert _find_reorder_channels_call(run_output) is None


def test_find_list_channels_call() -> None:
    run_output = RunOutput(status=RunStatus.completed, tools=[_tool_execution("list_channels", {})])
    assert _find_list_channels_call(run_output) is True
    assert _find_list_channels_call(RunOutput(status=RunStatus.completed, tools=None)) is False


# --- end-to-end dispatch -------------------------------------------------


def _create_agent(
    client: TestClient, headers: dict[str, str], name: str, pattern: str = "go"
) -> str:
    """A keyword rule (matched against "go", present in every triggering
    message below but never in the fake agent reply "ok") rather than
    "always" -- same "keyword, not always" precaution test_handoff.py's
    helper takes, so the agent's own reply (re-dispatched recursively,
    FR-5.6) can't re-trigger itself into an infinite tool-call loop."""
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
    team = client.post(
        "/api/v1/teams", json={"name": f"Channel Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"channel-tool-test-{agent_id}"}, headers=headers
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


def test_create_channel_creates_channel(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Creator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_channel")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("create_channel", {"name": "new-channel", "description": "fresh"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a channel"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "created channel /new-channel" in messages[2]["content"]

    listed = client.get("/api/v1/channels", headers=auth_headers).json()
    assert any(c["name"] == "new-channel" and c["description"] == "fresh" for c in listed)


def test_create_channel_rejects_short_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "PickyCreator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_channel")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_channel", {"name": "ab"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a channel"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "3-80 characters" in messages[2]["content"]


def test_create_channel_rejects_duplicate_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "DupeCreator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_channel")
    existing_name = f"channel-tool-test-{agent_id}"

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_channel", {"name": existing_name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a channel"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "already exists" in messages[2]["content"]


def test_update_channel_renames_by_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Renamer")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_channel")
    old_name = f"channel-tool-test-{agent_id}"

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("update_channel", {"channel": old_name, "name": "renamed-channel"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go rename it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "updated channel" in messages[2]["content"]

    updated = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert updated["name"] == "renamed-channel"


def test_update_channel_no_changes_specified_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Indecisive")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_channel")
    name = f"channel-tool-test-{agent_id}"

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("update_channel", {"channel": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "didn't specify any changes" in messages[2]["content"]


def test_update_channel_unknown_reference_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Confused")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_channel")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("update_channel", {"channel": "no-such-channel", "name": "x-y-z"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no channel with that id or name" in messages[2]["content"]


def test_archive_then_unarchive_channel_by_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Archiver")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, agent_id, "archive_channel", "unarchive_channel"
    )
    name = f"channel-tool-test-{agent_id}"

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("archive_channel", {"channel": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go archive it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "archived channel" in messages[2]["content"]

    archived = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert archived["archived"] is True

    # A name lookup for a channel already known by id still works after
    # archiving -- idx_channel_name only enforces uniqueness while
    # archived=0, but there's only one row with this name here.
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("unarchive_channel", {"channel": channel_id})),
    )
    rivulet2 = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go bring it back"},
        headers=auth_headers,
    )
    rivulet2_id = rivulet2.json()["id"]
    messages2 = client.get(f"/api/v1/rivulets/{rivulet2_id}/messages", headers=auth_headers).json()
    assert "unarchived channel" in messages2[-1]["content"]

    restored = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert restored["archived"] is False


def test_archive_already_archived_channel_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "DoubleArchiver")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "archive_channel")
    client.delete(f"/api/v1/channels/{channel_id}", headers=auth_headers)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("archive_channel", {"channel": channel_id})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go archive it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "already archived" in messages[2]["content"]


def test_reorder_channels_sets_position(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Reorderer")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "reorder_channels")

    other = client.post(
        "/api/v1/channels", json={"name": f"other-{agent_id}"}, headers=auth_headers
    )
    other_id = other.json()["id"]

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("reorder_channels", {"order": [other_id, channel_id]})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go reorder"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "reordered 2 channels" in messages[2]["content"]

    listed = client.get("/api/v1/channels", headers=auth_headers).json()
    ids_in_order = [c["id"] for c in listed if c["id"] in (other_id, channel_id)]
    assert ids_in_order == [other_id, channel_id]


def test_reorder_channels_unknown_ref_leaves_positions_unchanged(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "BadReorderer")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "reorder_channels")
    before = client.get("/api/v1/channels", headers=auth_headers).json()

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("reorder_channels", {"order": [channel_id, "no-such-channel"]})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go reorder"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "doesn't match any channel" in messages[2]["content"]

    after = client.get("/api/v1/channels", headers=auth_headers).json()
    assert before == after


def test_list_channels_reports_existing_channels(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Lister")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    name = f"channel-tool-test-{agent_id}"

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_channels", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go list channels"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert name in messages[2]["content"]


def test_create_channel_call_ignored_without_channels_manage_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#240: a completed run's `create_channel` tool call must not create a
    real channel unless the agent that made it actually holds the
    "channels:manage" scope, re-checked fresh at invocation time. See
    test_invite_tools.py's equivalent test for the full rationale."""
    agent_id = _create_agent(client, auth_headers, "UnauthorizedCreator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("create_channel", {"name": "sneaky-channel", "description": "nope"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a channel"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    listed = client.get("/api/v1/channels", headers=auth_headers).json()
    assert not any(c["name"] == "sneaky-channel" for c in listed)

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert all("created channel" not in m["content"] for m in messages)
