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
agent_hive.api.mcp_servers.discover_tools directly, mirroring how
test_thread_dispatch.py monkeypatches dispatch.service.run_agent.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_hive.agentos.mcp import DiscoveredTool, MCPConnectionError, discover_tools


class _FakeMCPTools:
    """Stands in for agno.tools.mcp.MCPTools with scripted behavior."""

    def __init__(self, *, on_connect: BaseException | None, functions: dict[str, Any]) -> None:
        self._on_connect = on_connect
        self._functions = functions
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


class _FakeFunction:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


def _patch_mcp_tools(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_connect: BaseException | None = None,
    functions: dict[str, Any] | None = None,
) -> _FakeMCPTools:
    fake = _FakeMCPTools(on_connect=on_connect, functions=functions or {})

    def _fake_mcp_tools(**_kwargs: Any) -> _FakeMCPTools:
        return fake

    monkeypatch.setattr("agent_hive.agentos.mcp.MCPTools", _fake_mcp_tools)
    return fake


async def test_discover_tools_returns_discovered_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_mcp_tools(
        monkeypatch,
        functions={
            "add": _FakeFunction("add", "Add two numbers together."),
            "get_weather": _FakeFunction("get_weather", ""),
        },
    )

    result = await discover_tools("http://127.0.0.1:9999/mcp")

    assert result == [
        DiscoveredTool(name="add", description="Add two numbers together."),
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


def _patch_discover_tools(
    monkeypatch: pytest.MonkeyPatch, result: Sequence[DiscoveredTool] | BaseException
) -> None:
    async def _fake(url: str, timeout_seconds: int = 10) -> list[DiscoveredTool]:  # noqa: ARG001
        if isinstance(result, BaseException):
            raise result
        return list(result)

    monkeypatch.setattr("agent_hive.api.mcp_servers.discover_tools", _fake)


def test_register_mcp_server_success(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_discover_tools(
        monkeypatch,
        [
            DiscoveredTool(name="add", description="Add two numbers together."),
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
