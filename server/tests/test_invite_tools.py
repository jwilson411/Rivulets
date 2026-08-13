"""#193: agent-facing workspace invite management -- the create_invite/
list_invites/revoke_invite tools (tools/builtin/invites.py) and their
detection + handling in dispatch/service.py. Mirrors test_workflow_tools.py's
style: agentos.run_agent is monkeypatched to hand back a RunOutput with
the tool call already baked in, since real tool-call detection by a live
model isn't something a fake API key can produce.
"""

from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.service import (
    _find_create_invite_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_invites_call,  # pyright: ignore[reportPrivateUsage]
    _find_revoke_invite_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.invites import create_invite, list_invites, revoke_invite
from tests.conftest import authorize_agent_for_builtin_tool


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


# --- tool entrypoints -------------------------------------------------


def test_create_invite_tool_returns_confirmation_string() -> None:
    assert create_invite.entrypoint is not None
    assert "invite" in create_invite.entrypoint().lower()


def test_list_invites_tool_returns_confirmation_string() -> None:
    assert list_invites.entrypoint is not None
    assert "invite" in list_invites.entrypoint().lower()


def test_revoke_invite_tool_returns_confirmation_string() -> None:
    assert revoke_invite.entrypoint is not None
    assert "abc123" in revoke_invite.entrypoint(invite_id="abc123")


# --- tool-call parsers --------------------------------------------------


def test_find_create_invite_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "create_invite",
                {"display_name_hint": "Alex", "max_uses": 3, "expires_in_hours": 24},
            )
        ],
    )
    call = _find_create_invite_call(run_output)
    assert call is not None
    assert call.display_name_hint == "Alex"
    assert call.max_uses == 3
    assert call.expires_in_hours == 24


def test_find_create_invite_call_defaults_missing_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("create_invite", {})]
    )
    call = _find_create_invite_call(run_output)
    assert call is not None
    assert call.display_name_hint is None
    assert call.max_uses == 1
    assert call.expires_in_hours == 168


def test_find_list_invites_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("list_invites", {})]
    )
    assert _find_list_invites_call(run_output) is True
    assert _find_list_invites_call(RunOutput(status=RunStatus.completed, tools=None)) is False


def test_find_revoke_invite_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("revoke_invite", {"invite_id": "inv-1"})],
    )
    assert _find_revoke_invite_call(run_output) == "inv-1"


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
    team = client.post(
        "/api/v1/teams", json={"name": f"Invite Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"invite-tool-test-{agent_id}"}, headers=headers
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


def _create_invite_via_api(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post("/api/v1/invites", json={}, headers=headers)
    assert created.status_code == 201, created.text
    invite_id: str = created.json()["invite_id"]
    return invite_id


def test_create_invite_creates_invite_and_posts_link(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Inviter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_invite")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_invite", {"display_name_hint": "Alex"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go invite someone"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    content = messages[2]["content"]
    assert "created a workspace invite" in content
    assert "Alex" in content

    listed = client.get("/api/v1/invites", headers=auth_headers).json()
    assert any(inv["display_name_hint"] == "Alex" for inv in listed)


def test_create_invite_confirmation_message_never_contains_the_secret(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#241: the raw secret must never be persisted into a synced Message
    -- it's a one-shot value, only ever delivered via the non-synced SSE
    `system_alert` payload (dispatch/service.py's publish() call inside
    _handle_create_invite_trigger)."""
    agent_id = _create_agent(client, auth_headers, "SecretlessInviter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_invite", {"display_name_hint": "Alex"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go invite someone"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    content = messages[2]["content"]
    # No URL, no path, no bearer token -- just id/hint/expiry.
    assert "http://" not in content
    assert "/invite/" not in content

    listed = client.get("/api/v1/invites", headers=auth_headers).json()
    invite_id = next(inv["id"] for inv in listed if inv["display_name_hint"] == "Alex")
    assert invite_id in content


def test_create_invite_does_not_publish_to_sync(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invites are never synced (db/models.py's Invite docstring) --
    publish_current_state must never be called for one, the same
    treatment api/invites.py's create_invite handler already gives it."""
    agent_id = _create_agent(client, auth_headers, "QuietInviter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_invite")

    published: list[tuple[str, str]] = []

    async def fake_publish(_db: object, entity_type: str, entity_id: str) -> None:
        published.append((entity_type, entity_id))

    monkeypatch.setattr("rivulets.dispatch.service.publish_current_state", fake_publish)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_invite", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go invite someone"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers)
    assert all(entity_type != "invite" for entity_type, _ in published)


def test_list_invites_reports_existing_invites(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Lister")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    invite_id = _create_invite_via_api(client, auth_headers)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "list_invites")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_invites", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go list invites"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert invite_id in messages[2]["content"]
    assert "active" in messages[2]["content"]


def test_list_invites_reports_no_invites(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "EmptyLister")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "list_invites")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_invites", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go list invites"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "there are none yet" in messages[2]["content"]


def test_revoke_invite_revokes_invite(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Revoker")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    invite_id = _create_invite_via_api(client, auth_headers)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "revoke_invite")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("revoke_invite", {"invite_id": invite_id})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go revoke it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "revoked invite" in messages[2]["content"]

    listed = client.get("/api/v1/invites", headers=auth_headers).json()
    revoked = next(inv for inv in listed if inv["id"] == invite_id)
    assert revoked["revoked"] is True


def test_revoke_invite_unknown_id_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ConfusedRevoker")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "revoke_invite")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("revoke_invite", {"invite_id": "no-such-invite"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go revoke it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no invite with that id" in messages[2]["content"]


def test_create_invite_call_ignored_without_invites_manage_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#240: a completed run's `create_invite` tool call must not create a
    real invite unless the agent that made it actually holds the
    "invites:manage" scope, re-checked fresh at invocation time -- not
    just because the run_output happens to contain a tool call with that
    name. Deliberately does *not* assign create_invite to the agent at
    all (mirroring the name-collision scenario the issue describes: the
    agent could just as easily have a same-named custom/MCP tool), so no
    system message about the (refused) attempt is expected either -- the
    run completes normally with only its own reply persisted."""
    agent_id = _create_agent(client, auth_headers, "UnauthorizedInviter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_invite", {"display_name_hint": "Eve"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go invite someone"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    listed = client.get("/api/v1/invites", headers=auth_headers).json()
    assert not any(inv["display_name_hint"] == "Eve" for inv in listed)

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert all("created a workspace invite" not in m["content"] for m in messages)
