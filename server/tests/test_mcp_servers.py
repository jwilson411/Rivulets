"""MCP server discovery (FR-8.5).

agentos.mcp.discover_tools() is tested directly by monkeypatching
agno.tools.mcp.MCPTools, so these run without a real MCP server. The
CancelledError case is a regression test: MCPTools' internal timeout
handling raises asyncio.CancelledError (a BaseException, not an Exception)
when a connection stalls, which a naive `except Exception` doesn't catch —
see agentos/mcp.py's docstring for the real-world traceback this caused
(an unrelated 500 from FastAPI's own request-scope teardown instead of a
graceful "can't reach this server").

The HTTP layer (api/mcp_servers.py) is tested by monkeypatching
rivulets.api.mcp_servers.discover_tools directly, mirroring how
test_rivulet_dispatch.py monkeypatches dispatch.service.run_agent.
"""

import asyncio
import socket
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rivulets.agentos.mcp import DiscoveredTool, MCPConnectionError, discover_tools
from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope


class _FakeMCPTools:
    """Stands in for agno.tools.mcp.MCPTools with scripted behavior."""

    def __init__(
        self,
        *,
        on_connect: BaseException | None,
        functions: dict[str, Any],
        on_close: BaseException | None = None,
    ) -> None:
        self._on_connect = on_connect
        self._functions = functions
        self._on_close = on_close
        self.closed = False

    async def connect(self) -> None:
        if self._on_connect is not None:
            raise self._on_connect

    async def initialize(self) -> None:
        pass

    def get_functions(self) -> dict[str, Any]:
        return self._functions

    async def close(self) -> None:
        self.closed = True
        if self._on_close is not None:
            raise self._on_close


class _FakeFunction:
    def __init__(
        self, name: str, description: str, parameters: dict[str, Any] | None = None
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters or {}


def _patch_mcp_tools(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_connect: BaseException | None = None,
    functions: dict[str, Any] | None = None,
    on_close: BaseException | None = None,
) -> _FakeMCPTools:
    fake = _FakeMCPTools(on_connect=on_connect, functions=functions or {}, on_close=on_close)

    def _fake_mcp_tools(**_kwargs: Any) -> _FakeMCPTools:
        return fake

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)
    return fake


async def test_discover_tools_returns_discovered_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    add_schema = {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "dependentRequired": {"a": ["b"]},
    }
    fake = _patch_mcp_tools(
        monkeypatch,
        functions={
            "add": _FakeFunction("add", "Add two numbers together.", parameters=add_schema),
            "get_weather": _FakeFunction("get_weather", ""),
        },
    )

    result = await discover_tools("http://127.0.0.1:9999/mcp")

    assert result == [
        DiscoveredTool(
            name="add", description="Add two numbers together.", input_schema=add_schema
        ),
        DiscoveredTool(name="get_weather", description=""),
    ]
    assert fake.closed is True


async def test_discover_tools_wraps_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_mcp_tools(monkeypatch, on_connect=ConnectionRefusedError("refused"))

    with pytest.raises(MCPConnectionError, match="refused"):
        await discover_tools("http://127.0.0.1:1/mcp")


async def test_discover_tools_wraps_cancelled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: a stalled connection surfaces as CancelledError from
    within MCPTools (a BaseException), not a plain Exception. It must still
    come out of discover_tools() as a catchable MCPConnectionError instead of
    propagating raw and corrupting the caller's cancel-scope state."""
    _patch_mcp_tools(monkeypatch, on_connect=asyncio.CancelledError("cancelled"))

    with pytest.raises(MCPConnectionError):
        await discover_tools("http://127.0.0.1:1/mcp", timeout_seconds=1)


async def test_discover_tools_closes_even_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_mcp_tools(monkeypatch, on_connect=RuntimeError("boom"))

    with pytest.raises(MCPConnectionError):
        await discover_tools("http://127.0.0.1:9999/mcp")

    assert fake.closed is True


async def test_discover_tools_passes_headers_to_mcp_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """#124: headers must reach agno's MCPTools as a StreamableHTTPClientParams
    server_params, not silently get dropped."""
    captured: dict[str, Any] = {}

    def _fake_mcp_tools(**kwargs: Any) -> _FakeMCPTools:
        captured.update(kwargs)
        return _FakeMCPTools(on_connect=None, functions={"add": _FakeFunction("add", "Adds.")})

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)

    await discover_tools(
        "http://127.0.0.1:9999/mcp", headers={"Authorization": "Bearer secret-token"}
    )

    server_params = captured["server_params"]
    assert server_params is not None
    assert server_params.headers == {"Authorization": "Bearer secret-token"}
    assert server_params.url == "http://127.0.0.1:9999/mcp"


async def test_discover_tools_omits_server_params_when_no_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: passing server_params unconditionally would swap
    in StreamableHTTPClientParams' own 30s default timeout in place of
    discover_tools' own timeout_seconds for every headerless server."""
    captured: dict[str, Any] = {}

    def _fake_mcp_tools(**kwargs: Any) -> _FakeMCPTools:
        captured.update(kwargs)
        return _FakeMCPTools(on_connect=None, functions={})

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)

    await discover_tools("http://127.0.0.1:9999/mcp")

    assert captured["server_params"] is None


async def test_discover_tools_pin_public_uses_pinned_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#477: invite-grant / agent-driven discover must pin connect, but
    still carry timeout_seconds so we don't regress the 30s-default
    swap the test above guards."""
    from datetime import timedelta

    from rivulets.agentos.mcp import (
        PublicStreamableHTTPClientParams,
        create_public_mcp_http_client,
    )

    captured: dict[str, Any] = {}

    def _fake_mcp_tools(**kwargs: Any) -> _FakeMCPTools:
        captured.update(kwargs)
        return _FakeMCPTools(on_connect=None, functions={})

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)

    await discover_tools("http://mcp.example.com/mcp", timeout_seconds=7, pin_public=True)

    server_params = captured["server_params"]
    assert isinstance(server_params, PublicStreamableHTTPClientParams)
    assert server_params.httpx_client_factory is create_public_mcp_http_client
    assert server_params.timeout == timedelta(seconds=7)


async def test_discover_tools_passes_command_and_env_to_mcp_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#187: a stdio server's command/args/env must reach agno's MCPTools
    as a single shlex-joined `command` string (so agno's own
    prepare_command validation runs) plus `env`/`transport="stdio"` --
    not silently dropped or routed through the streamable-http path."""
    captured: dict[str, Any] = {}

    def _fake_mcp_tools(**kwargs: Any) -> _FakeMCPTools:
        captured.update(kwargs)
        return _FakeMCPTools(on_connect=None, functions={"run": _FakeFunction("run", "Runs.")})

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)

    result = await discover_tools(command="npx", args=["-y", "@foo/bar"], env={"API_KEY": "secret"})

    assert captured["command"] == "npx -y @foo/bar"
    assert captured["env"] == {"API_KEY": "secret"}
    assert captured["transport"] == "stdio"
    assert result == [DiscoveredTool(name="run", description="Runs.")]


async def test_discover_tools_shlex_quotes_args_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_mcp_tools(**kwargs: Any) -> _FakeMCPTools:
        captured.update(kwargs)
        return _FakeMCPTools(on_connect=None, functions={})

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)

    await discover_tools(command="python3", args=["/path/with spaces/server.py"])

    assert captured["command"] == "python3 '/path/with spaces/server.py'"


async def test_discover_tools_wraps_stdio_construction_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command agno's own prepare_command() rejects (shell metacharacters,
    disallowed executable) must degrade to MCPConnectionError like any
    other connection failure, not propagate the raw ValueError."""

    def _fake_mcp_tools(**_kwargs: Any) -> _FakeMCPTools:
        raise ValueError("MCP command needs to use one of the following executables: {...}")

    monkeypatch.setattr("rivulets.agentos.mcp.MCPTools", _fake_mcp_tools)

    with pytest.raises(MCPConnectionError, match="executables"):
        await discover_tools(command="curl", args=["evil.example"])


async def test_discover_tools_succeeds_even_when_closing_the_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while tearing down the (already-successful) MCP connection
    must be logged and swallowed, not raised -- the caller already has a
    good result by the time close() runs in the `finally` block."""
    fake = _patch_mcp_tools(
        monkeypatch,
        functions={"add": _FakeFunction("add", "Adds numbers.")},
        on_close=RuntimeError("connection already gone"),
    )

    result = await discover_tools("http://127.0.0.1:9999/mcp")

    assert result == [DiscoveredTool(name="add", description="Adds numbers.")]
    assert fake.closed is True


def _patch_discover_tools(
    monkeypatch: pytest.MonkeyPatch,
    result: Sequence[DiscoveredTool] | BaseException,
    *,
    capture_headers: list[dict[str, str] | None] | None = None,
    capture_env: list[dict[str, str] | None] | None = None,
    capture_commands: list[str | None] | None = None,
) -> None:
    async def _fake(
        url: str | None = None,  # noqa: ARG001
        timeout_seconds: int = 10,  # noqa: ARG001
        headers: dict[str, str] | None = None,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        pin_public: bool = False,  # noqa: ARG001
    ) -> list[DiscoveredTool]:
        if capture_headers is not None:
            capture_headers.append(headers)
        if capture_env is not None:
            capture_env.append(env)
        if capture_commands is not None:
            capture_commands.append(f"{command} {args}" if command else None)
        if isinstance(result, BaseException):
            raise result
        return list(result)

    monkeypatch.setattr("rivulets.api.mcp_servers.discover_tools", _fake)


def test_register_mcp_server_success(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    add_schema = {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "dependentRequired": {"a": ["b"]},
    }
    _patch_discover_tools(
        monkeypatch,
        [
            DiscoveredTool(
                name="add", description="Add two numbers together.", input_schema=add_schema
            ),
            DiscoveredTool(name="get_weather", description=""),
        ],
    )

    response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Test server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["connected"] is True
    assert body["last_connected_at"] is not None
    assert {t["name"] for t in body["tools"]} == {"add", "get_weather"}
    assert all(t["mcp_tool_name"] for t in body["tools"])
    tools_by_name = {t["name"]: t for t in body["tools"]}
    assert tools_by_name["add"]["input_schema"] == add_schema
    assert tools_by_name["get_weather"]["input_schema"] == {}


def test_register_mcp_server_degrades_gracefully_on_connection_failure(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-2.4: an unreachable MCP server still gets its row persisted,
    just with connected=False and no tools — not an error response."""
    _patch_discover_tools(monkeypatch, MCPConnectionError("could not connect"))

    response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Dead server", "url": "http://127.0.0.1:1/mcp"},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["connected"] is False
    assert body["last_connected_at"] is None
    assert body["tools"] == []

    listed = client.get("/api/v1/mcp-servers", headers=auth_headers)
    assert len(listed.json()) == 1
    assert listed.json()[0]["connected"] is False


def test_registered_mcp_tools_appear_in_tools_list(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])

    client.post(
        "/api/v1/mcp-servers",
        json={"name": "Test server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )

    tools = client.get("/api/v1/tools", headers=auth_headers)
    assert tools.status_code == 200
    mcp_tools = [t for t in tools.json() if t["tool_type"] == "mcp"]
    assert len(mcp_tools) == 1
    assert mcp_tools[0]["name"] == "add"


def test_get_mcp_server_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/mcp-servers/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_reconnect_mcp_server(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, MCPConnectionError("down"))
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Flaky server", "url": "http://127.0.0.1:1/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    assert created.json()["connected"] is False

    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    reconnected = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=auth_headers)

    assert reconnected.status_code == 200
    assert reconnected.json()["connected"] is True
    assert [t["name"] for t in reconnected.json()["tools"]] == ["add"]


def test_reconnect_mcp_server_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/mcp-servers/nonexistent/reconnect", headers=auth_headers)
    assert response.status_code == 404


def test_unregister_mcp_server_removes_server_and_tools(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Test server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers)
    assert deleted.status_code == 204

    assert client.get(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers).status_code == 404
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    assert all(t["tool_type"] != "mcp" for t in tools)


async def test_unregister_mcp_server_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#287: the `client` fixture never actually starts the sync engine, so
    a successful delete queues a tombstone retry (SyncPendingOutbound.
    deleted=True) instead of the delete never reaching any peer at all --
    mirrors test_teams_api.py's equivalent for delete_team."""
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Doomed server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers)
    assert deleted.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("mcp_server", server_id))
        assert pending is not None
        assert pending.deleted is True


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


# #231: unregister/reconnect are owner-gated for a stdio server (local
# subprocess execution) or any server holding stored headers/env
# (keychain secrets) -- see api/mcp_servers.py's _requires_owner_to_mutate.


def test_unregister_stdio_mcp_server_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="list_dir", description="Lists.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Filesystem tools", "transport": "stdio", "command": "npx"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=invite_headers)
    assert response.status_code == 403

    still_there = client.get(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers)
    assert still_there.status_code == 200


def test_reconnect_stdio_mcp_server_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="list_dir", description="Lists.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Filesystem tools", "transport": "stdio", "command": "npx"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=invite_headers)
    assert response.status_code == 403


def test_unregister_mcp_server_with_headers_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Authed server",
            "url": "http://127.0.0.1:9999/mcp",
            "headers": {"Authorization": "Bearer sk-real-secret"},
        },
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=invite_headers)
    assert response.status_code == 403


def test_reconnect_mcp_server_with_headers_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Authed server",
            "url": "http://127.0.0.1:9999/mcp",
            "headers": {"Authorization": "Bearer sk-real-secret"},
        },
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=invite_headers)
    assert response.status_code == 403


def _patch_getaddrinfo(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    """Makes every hostname resolve to `ip`, so tests control what
    security/network.py's check_host_is_public sees."""

    def fake_getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return [(None, None, None, None, (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_unregister_plain_streamable_http_mcp_server_stays_open_to_invite_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server with no stdio transport and no stored headers/env carries
    nothing sensitive to protect -- unregistering/reconnecting it stays
    open to any grant, unchanged from before #231. Since #365 an
    invite-grant reconnect re-runs the SSRF check on the stored url, so
    this uses a public-resolving hostname; the private-resolving case is
    test_reconnect_mcp_server_at_a_private_address_requires_owner_grant."""
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    _patch_getaddrinfo(monkeypatch, "93.184.216.34")
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Plain server", "url": "http://mcp.example.com/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    reconnected = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=invite_headers)
    assert reconnected.status_code == 200, reconnected.text

    deleted = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=invite_headers)
    assert deleted.status_code == 204


def test_reconnect_mcp_server_at_a_private_address_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#365: reconnect re-runs check_host_is_public on the stored url for
    a non-owner session, keyed off the *caller* -- rows don't record who
    registered a server, so an invite-grant can't reconnect any server
    whose url currently resolves private, even one the owner deliberately
    registered. The owner keeps reconnecting it, unchanged (the supported
    self-hosted local MCP server case)."""
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Local server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=invite_headers)
    assert response.status_code == 403

    owner_response = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=auth_headers)
    assert owner_response.status_code == 200, owner_response.text


def test_reconnect_mcp_server_after_dns_rebind_is_blocked_for_invite_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#365's exact repro: an invite-grant registers a headerless
    streamable-http server on a hostname that resolves public (so the
    register-time SSRF check passes), the name's DNS is then re-pointed
    at loopback, and the same grant POSTs /reconnect. Before the fix,
    _connect_and_sync_tools dialed whatever the name resolves to *now*;
    reconnect must re-resolve and re-check instead."""
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    invite_headers = _invite_headers(client, auth_headers)

    _patch_getaddrinfo(monkeypatch, "93.184.216.34")
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Rebinder", "url": "http://rebind.example.com/mcp"},
        headers=invite_headers,
    )
    assert created.status_code == 201, created.text
    server_id = created.json()["id"]

    # The attacker flips the name's A record to loopback.
    _patch_getaddrinfo(monkeypatch, "127.0.0.1")
    response = client.post(f"/api/v1/mcp-servers/{server_id}/reconnect", headers=invite_headers)
    assert response.status_code == 403
    assert "internal/private network address" in response.json()["detail"]


def test_register_mcp_server_with_headers_stores_names_not_values(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str] | None] = []
    _patch_discover_tools(
        monkeypatch, [DiscoveredTool(name="add", description="Adds.")], capture_headers=captured
    )

    response = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Authed server",
            "url": "http://127.0.0.1:9999/mcp",
            "headers": {"Authorization": "Bearer sk-real-secret"},
        },
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["header_names"] == ["Authorization"]
    assert "headers" not in body
    assert "Bearer sk-real-secret" not in response.text
    # discover_tools (and thus the actual MCP handshake) received the real
    # header value, even though the response never does.
    assert captured == [{"Authorization": "Bearer sk-real-secret"}]

    listed = client.get("/api/v1/mcp-servers", headers=auth_headers).json()
    assert listed[0]["header_names"] == ["Authorization"]


def test_register_mcp_server_with_headers_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Authed server",
            "url": "http://127.0.0.1:9999/mcp",
            "headers": {"Authorization": "Bearer sk-real-secret"},
        },
        headers=invite_headers,
    )
    assert response.status_code == 403

    # Registering without headers stays open to any grant -- as long as
    # the URL resolves to a public address (see the SSRF tests below for
    # the loopback/private case, which *is* blocked for a non-owner grant
    # now, unlike before this guard existed).
    def fake_getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    plain = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Plain server", "url": "http://mcp.example.com/mcp"},
        headers=invite_headers,
    )
    assert plain.status_code == 201, plain.text


def test_register_mcp_server_at_a_private_address_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SSRF guard (security/network.py's check_host_is_public): a
    non-owner (invite-grant) session can't point a streamable-http
    registration at this node's own loopback/LAN services, even with no
    headers involved. An owner can -- a self-hosted local MCP server is a
    real, supported use case (the test above)."""
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Internal server", "url": "http://127.0.0.1:9999/mcp"},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # An owner session registering the exact same loopback URL still
    # works, unchanged (this is the existing, tested "local MCP server"
    # path exercised throughout the rest of this file).
    owner_response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Internal server (owner)", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    assert owner_response.status_code == 201, owner_response.text


def test_set_mcp_server_headers_replaces_and_reconnects(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str] | None] = []
    _patch_discover_tools(monkeypatch, MCPConnectionError("no auth"), capture_headers=captured)
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    assert created.json()["connected"] is False

    _patch_discover_tools(
        monkeypatch, [DiscoveredTool(name="add", description="Adds.")], capture_headers=captured
    )
    updated = client.put(
        f"/api/v1/mcp-servers/{server_id}/headers",
        json={"headers": {"X-Api-Key": "k-123"}},
        headers=auth_headers,
    )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["header_names"] == ["X-Api-Key"]
    assert body["connected"] is True
    assert captured[-1] == {"X-Api-Key": "k-123"}

    # Clearing (empty dict) removes them and drops back to a headerless
    # reconnect.
    cleared = client.put(
        f"/api/v1/mcp-servers/{server_id}/headers", json={"headers": {}}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["header_names"] == []
    assert captured[-1] is None


def test_set_mcp_server_headers_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.put(
        f"/api/v1/mcp-servers/{server_id}/headers",
        json={"headers": {"Authorization": "Bearer x"}},
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_set_mcp_server_headers_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/v1/mcp-servers/nonexistent/headers", json={"headers": {}}, headers=auth_headers
    )
    assert response.status_code == 404


def test_unregister_mcp_server_with_headers_deletes_stored_secret(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.agentos.mcp import mcp_header_ref
    from rivulets.security.credentials import CredentialStoreError, get_secret

    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Authed server",
            "url": "http://127.0.0.1:9999/mcp",
            "headers": {"Authorization": "Bearer sk-real-secret"},
        },
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    ref = mcp_header_ref(server_id)
    assert get_secret(ref)  # sanity check: it was actually stored

    deleted = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers)
    assert deleted.status_code == 204

    with pytest.raises(CredentialStoreError):
        get_secret(ref)


# --- stdio transport (#187) ---


def test_register_stdio_mcp_server_success(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_commands: list[str | None] = []
    captured_env: list[dict[str, str] | None] = []
    _patch_discover_tools(
        monkeypatch,
        [DiscoveredTool(name="list_dir", description="Lists a directory.")],
        capture_commands=captured_commands,
        capture_env=captured_env,
    )

    response = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Filesystem tools",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
            "env": {"API_KEY": "secret-value"},
        },
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transport"] == "stdio"
    assert body["command"] == "npx"
    assert body["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    assert body["url"] is None
    assert body["connected"] is True
    assert body["env_names"] == ["API_KEY"]
    assert "env" not in body
    assert "secret-value" not in response.text
    assert captured_commands == ["npx ['-y', '@modelcontextprotocol/server-filesystem', '/data']"]
    assert captured_env == [{"API_KEY": "secret-value"}]


def test_register_stdio_mcp_server_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike streamable-http (only owner-gated when headers are set),
    every stdio registration is owner-gated -- command execution itself
    is the sensitive part, not just entering a credential."""
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Local tool", "transport": "stdio", "command": "npx"},
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_register_mcp_server_rejects_mismatched_transport_fields(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    missing_url = client.post("/api/v1/mcp-servers", json={"name": "No url"}, headers=auth_headers)
    assert missing_url.status_code == 422

    missing_command = client.post(
        "/api/v1/mcp-servers",
        json={"name": "No command", "transport": "stdio"},
        headers=auth_headers,
    )
    assert missing_command.status_code == 422

    mixed_fields = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Mixed",
            "transport": "stdio",
            "command": "npx",
            "url": "http://127.0.0.1:9999/mcp",
        },
        headers=auth_headers,
    )
    assert mixed_fields.status_code == 422


def test_set_mcp_server_env_replaces_and_reconnects(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_env: list[dict[str, str] | None] = []
    _patch_discover_tools(monkeypatch, MCPConnectionError("bad env"), capture_env=captured_env)
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Server", "transport": "stdio", "command": "npx"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    assert created.json()["connected"] is False

    _patch_discover_tools(
        monkeypatch, [DiscoveredTool(name="run", description="Runs.")], capture_env=captured_env
    )
    updated = client.put(
        f"/api/v1/mcp-servers/{server_id}/env",
        json={"env": {"TOKEN": "abc"}},
        headers=auth_headers,
    )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["env_names"] == ["TOKEN"]
    assert body["connected"] is True
    assert captured_env[-1] == {"TOKEN": "abc"}

    cleared = client.put(
        f"/api/v1/mcp-servers/{server_id}/env", json={"env": {}}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["env_names"] == []
    assert captured_env[-1] is None


def test_set_mcp_server_env_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="run", description="Runs.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Server", "transport": "stdio", "command": "npx"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.put(
        f"/api/v1/mcp-servers/{server_id}/env",
        json={"env": {"TOKEN": "x"}},
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_set_mcp_server_env_rejects_streamable_http_server(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="add", description="Adds.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]

    response = client.put(
        f"/api/v1/mcp-servers/{server_id}/env",
        json={"env": {"TOKEN": "x"}},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_set_mcp_server_headers_rejects_stdio_server(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="run", description="Runs.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={"name": "Server", "transport": "stdio", "command": "npx"},
        headers=auth_headers,
    )
    server_id = created.json()["id"]

    response = client.put(
        f"/api/v1/mcp-servers/{server_id}/headers",
        json={"headers": {"Authorization": "Bearer x"}},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_unregister_stdio_mcp_server_deletes_stored_env_secret(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.agentos.mcp import mcp_env_ref
    from rivulets.security.credentials import CredentialStoreError, get_secret

    _patch_discover_tools(monkeypatch, [DiscoveredTool(name="run", description="Runs.")])
    created = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "Local tool",
            "transport": "stdio",
            "command": "npx",
            "env": {"TOKEN": "secret"},
        },
        headers=auth_headers,
    )
    server_id = created.json()["id"]
    ref = mcp_env_ref(server_id)
    assert get_secret(ref)  # sanity check: it was actually stored

    deleted = client.delete(f"/api/v1/mcp-servers/{server_id}", headers=auth_headers)
    assert deleted.status_code == 204

    with pytest.raises(CredentialStoreError):
        get_secret(ref)
