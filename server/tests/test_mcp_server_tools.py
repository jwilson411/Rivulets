"""#191: agent-facing MCP server management -- the register_mcp_server/
reconnect_mcp_server/delete_mcp_server/list_mcp_servers tools (tools/
builtin/mcp_servers.py) and their detection + handling in dispatch/
service.py. Mirrors test_channel_tools.py's style: agentos.run_agent is
monkeypatched to hand back a RunOutput with the tool call already baked
in, and dispatch.service.discover_tools is monkeypatched the same way
test_mcp_servers.py monkeypatches api.mcp_servers.discover_tools, since a
real MCP handshake isn't something these tests can produce.
"""

import json
import socket
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.agentos.mcp import DiscoveredTool, MCPConnectionError
from rivulets.db.models import MCPServer, SyncPendingOutbound
from rivulets.db.session import session_scope
from rivulets.dispatch.service import (
    _find_delete_mcp_server_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_mcp_servers_call,  # pyright: ignore[reportPrivateUsage]
    _find_reconnect_mcp_server_call,  # pyright: ignore[reportPrivateUsage]
    _find_register_mcp_server_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.mcp_servers import (
    delete_mcp_server,
    list_mcp_servers,
    reconnect_mcp_server,
    register_mcp_server,
)
from tests.conftest import authorize_agent_for_builtin_tool  # pyright: ignore[reportMissingImports]


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


def _patch_getaddrinfo_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """#285: register_mcp_server's trigger handler now runs every url
    through check_host_is_public unconditionally (dispatch/service.py's
    _handle_register_mcp_server_trigger docstring) -- same convention
    test_mcp_servers.py's SSRF tests use, so a plain hostname resolves to
    a real public IP without depending on actual DNS/network access."""

    def fake_getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# --- tool entrypoints -------------------------------------------------


def test_register_mcp_server_tool_returns_confirmation_string() -> None:
    assert register_mcp_server.entrypoint is not None
    result = register_mcp_server.entrypoint(name="Weather", url="http://127.0.0.1:9999/mcp")
    assert "Weather" in result
    assert "http://127.0.0.1:9999/mcp" in result


def test_reconnect_mcp_server_tool_returns_confirmation_string() -> None:
    assert reconnect_mcp_server.entrypoint is not None
    assert "Weather" in reconnect_mcp_server.entrypoint(server="Weather")


def test_delete_mcp_server_tool_returns_confirmation_string() -> None:
    assert delete_mcp_server.entrypoint is not None
    assert "Weather" in delete_mcp_server.entrypoint(server="Weather")


def test_list_mcp_servers_tool_returns_confirmation_string() -> None:
    assert list_mcp_servers.entrypoint is not None
    assert "mcp server" in list_mcp_servers.entrypoint().lower()


# --- tool-call parsers --------------------------------------------------


def test_find_register_mcp_server_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "register_mcp_server", {"name": "Weather", "url": "http://127.0.0.1:9999/mcp"}
            )
        ],
    )
    call = _find_register_mcp_server_call(run_output)
    assert call is not None
    assert call.name == "Weather"
    assert call.url == "http://127.0.0.1:9999/mcp"


def test_find_register_mcp_server_call_missing_url_returns_none() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("register_mcp_server", {"name": "Weather"})],
    )
    assert _find_register_mcp_server_call(run_output) is None


def test_find_reconnect_mcp_server_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("reconnect_mcp_server", {"server": "Weather"})],
    )
    assert _find_reconnect_mcp_server_call(run_output) == "Weather"


def test_find_delete_mcp_server_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("delete_mcp_server", {"server": "Weather"})],
    )
    assert _find_delete_mcp_server_call(run_output) == "Weather"


def test_find_list_mcp_servers_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("list_mcp_servers", {})]
    )
    assert _find_list_mcp_servers_call(run_output) is True
    assert _find_list_mcp_servers_call(RunOutput(status=RunStatus.completed, tools=None)) is False


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
        "/api/v1/teams", json={"name": f"MCP Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"mcp-tool-test-{agent_id}"}, headers=headers
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


def _patch_api_discover_tools(
    monkeypatch: pytest.MonkeyPatch, result: list[DiscoveredTool] | BaseException
) -> None:
    """Patches the HTTP layer's discover_tools -- used to set up an
    existing MCP server row via a plain POST before the actual tool-call
    test switches the patch target to dispatch.service.discover_tools."""

    async def _fake(*_args: object, **_kwargs: object) -> list[DiscoveredTool]:
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr("rivulets.api.mcp_servers.discover_tools", _fake)


def _patch_dispatch_discover_tools(
    monkeypatch: pytest.MonkeyPatch, result: list[DiscoveredTool] | BaseException
) -> None:
    async def _fake(*_args: object, **_kwargs: object) -> list[DiscoveredTool]:
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr("rivulets.dispatch.service.discover_tools", _fake)


def test_register_mcp_server_creates_server(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Registrar")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "register_mcp_server")

    _patch_dispatch_discover_tools(
        monkeypatch, [DiscoveredTool(name="add", description="Adds numbers.")]
    )
    _patch_getaddrinfo_public(monkeypatch)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "register_mcp_server",
                {"name": "Math server", "url": "http://mcp.example.com/mcp"},
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go register a server"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "registered MCP server 'Math server'" in messages[2]["content"]
    assert "connected" in messages[2]["content"]

    listed = client.get("/api/v1/mcp-servers", headers=auth_headers).json()
    assert any(s["name"] == "Math server" and s["connected"] is True for s in listed)
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    assert any(t["name"] == "add" and t["tool_type"] == "mcp" for t in tools)


def test_register_mcp_server_degrades_gracefully_on_connection_failure(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "FlakyRegistrar")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "register_mcp_server")

    _patch_dispatch_discover_tools(monkeypatch, MCPConnectionError("could not connect"))
    _patch_getaddrinfo_public(monkeypatch)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "register_mcp_server", {"name": "Dead server", "url": "http://dead.example.com/mcp"}
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go register a server"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "couldn't connect" in messages[2]["content"]

    listed = client.get("/api/v1/mcp-servers", headers=auth_headers).json()
    assert any(s["name"] == "Dead server" and s["connected"] is False for s in listed)


def test_register_mcp_server_missing_url_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Blank")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "register_mcp_server")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("register_mcp_server", {"name": "No URL", "url": "  "})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go register a server"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "didn't provide a url" in messages[2]["content"]

    listed = client.get("/api/v1/mcp-servers", headers=auth_headers).json()
    assert listed == []


def test_register_mcp_server_at_a_private_address_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#285: _handle_register_mcp_server_trigger runs every url through
    check_host_is_public unconditionally -- unlike the HTTP route, there's
    no "an owner deliberately chose this" exception here, since there's no
    live session behind an agent-driven tool call to know that from."""
    agent_id = _create_agent(client, auth_headers, "SSRFRegistrar")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "register_mcp_server")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "register_mcp_server", {"name": "Internal", "url": "http://127.0.0.1:9999/mcp"}
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go register a server"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "internal/private network address" in messages[2]["content"]

    listed = client.get("/api/v1/mcp-servers", headers=auth_headers).json()
    assert listed == []


def _register_server_via_api(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    url: str = "http://127.0.0.1:9999/mcp",
) -> str:
    _patch_api_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": name, "url": url},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    server_id: str = created.json()["id"]
    return server_id


def test_reconnect_mcp_server_by_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Reconnector")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "reconnect_mcp_server")
    server_name = f"reconnect-target-{agent_id}"
    # #365: the reconnect trigger now re-runs check_host_is_public on the
    # stored url, so the happy path needs a public-resolving one.
    _register_server_via_api(
        client, auth_headers, monkeypatch, server_name, url="http://mcp.example.com/mcp"
    )

    _patch_dispatch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    _patch_getaddrinfo_public(monkeypatch)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("reconnect_mcp_server", {"server": server_name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go reconnect it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "reconnected MCP server" in messages[2]["content"]


def test_reconnect_mcp_server_unknown_reference_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ConfusedReconnector")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "reconnect_mcp_server")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("reconnect_mcp_server", {"server": "no-such-server"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go reconnect it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no server with that id or name" in messages[2]["content"]


async def test_reconnect_mcp_server_with_stored_headers_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#285: api/mcp_servers.py's reconnect_mcp_server route requires a
    live owner session for a server holding stored headers/env
    (_requires_owner_to_mutate) -- the trigger handler has no live session
    to check that against, so it refuses outright rather than reusing the
    stored secret on any rivulet participant's say-so."""
    agent_id = _create_agent(client, auth_headers, "HeaderReconnector")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "reconnect_mcp_server")

    async with session_scope() as db:
        server = MCPServer(
            name="authed-server",
            transport="streamable-http",
            url="http://127.0.0.1:9999/mcp",
            header_names_json=json.dumps(["Authorization"]),
        )
        db.add(server)
        await db.commit()

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("reconnect_mcp_server", {"server": "authed-server"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go reconnect it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "requires a live owner session" in messages[2]["content"]


def test_reconnect_mcp_server_at_a_private_address_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#365: _handle_reconnect_mcp_server_trigger re-runs the stored url
    through check_host_is_public before dialing it again, unconditionally
    (same "no live session to grant an owner exception" reasoning as the
    register trigger). A url that resolved public at registration and was
    then DNS-rebound to loopback -- or, as here, one the owner registered
    at a private address via the API -- is refused instead of dialed."""
    agent_id = _create_agent(client, auth_headers, "SSRFReconnector")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "reconnect_mcp_server")
    server_name = f"private-reconnect-target-{agent_id}"
    _register_server_via_api(client, auth_headers, monkeypatch, server_name)

    _patch_dispatch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("reconnect_mcp_server", {"server": server_name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go reconnect it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "internal/private network address" in messages[2]["content"]


def test_delete_mcp_server_removes_server_and_tools(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Deleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_mcp_server")
    server_name = f"delete-target-{agent_id}"
    server_id = _register_server_via_api(client, auth_headers, monkeypatch, server_name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_mcp_server", {"server": server_id})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "deleted MCP server" in messages[2]["content"]

    assert client.get(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers).status_code == 404
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    assert all(t["tool_type"] != "mcp" for t in tools)


async def test_delete_mcp_server_trigger_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#287: an agent-triggered delete (dispatch/service.py's
    _handle_delete_mcp_server_trigger) must tombstone the same as the HTTP
    unregister_mcp_server route does."""
    agent_id = _create_agent(client, auth_headers, "SyncDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_mcp_server")
    server_name = f"delete-sync-target-{agent_id}"
    server_id = _register_server_via_api(client, auth_headers, monkeypatch, server_name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_mcp_server", {"server": server_id})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    client.get(f"/api/v1/rivulets/{rivulet.json()['id']}/messages", headers=auth_headers)

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("mcp_server", server_id))
        assert pending is not None
        assert pending.deleted is True


def test_delete_mcp_server_unknown_reference_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ConfusedDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_mcp_server")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_mcp_server", {"server": "no-such-server"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no server with that id or name" in messages[2]["content"]


async def test_delete_mcp_server_with_stored_headers_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#285: same "no live session to check owner-gating against" refusal
    as reconnect above, for delete -- mirrors api/mcp_servers.py's
    unregister_mcp_server requiring a live owner session for a server
    holding stored headers/env."""
    agent_id = _create_agent(client, auth_headers, "HeaderDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_mcp_server")

    async with session_scope() as db:
        server = MCPServer(
            name="authed-server",
            transport="streamable-http",
            url="http://127.0.0.1:9999/mcp",
            header_names_json=json.dumps(["Authorization"]),
        )
        db.add(server)
        await db.commit()
        server_id = server.id

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_mcp_server", {"server": "authed-server"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "requires a live owner session" in messages[2]["content"]

    assert client.get(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers).status_code == 200


def test_list_mcp_servers_reports_existing_servers(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Lister")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    server_name = f"list-target-{agent_id}"
    _register_server_via_api(client, auth_headers, monkeypatch, server_name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_mcp_servers", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go list servers"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert server_name in messages[2]["content"]
