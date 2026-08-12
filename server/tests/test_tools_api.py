"""Tools API (FR-8.2 through FR-8.4) HTTP-layer tests.

Custom-tool CRUD is exercised end-to-end here; agent-side resolution of
the resulting Tool rows into real agno objects is covered separately in
test_tool_resolution.py. Builtin tool rows only exist because app.py's
lifespan calls seed_builtin_tools() at startup -- the `client` fixture
triggers that via create_app(), so a builtin row is always present.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import rivulets.api.tools as tools_api


def _create_custom_tool(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    create = client.post(
        "/api/v1/tools",
        json={"name": "my_tool", "description": "Does a thing."},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    return create.json()


def _get_builtin_tool_id(client: TestClient, auth_headers: dict[str, str]) -> str:
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    builtin = next(t for t in tools if t["tool_type"] == "builtin")
    return builtin["id"]


def test_custom_tool_crud_lifecycle(client: TestClient, auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/v1/tools",
        json={"name": "my_tool", "description": "Does a thing."},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    tool = create.json()
    assert tool["tool_type"] == "custom"
    assert tool["source_path"] is not None

    renamed = client.patch(
        f"/api/v1/tools/{tool['id']}",
        json={"name": "renamed_tool"},
        headers=auth_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "renamed_tool"

    deleted = client.delete(f"/api/v1/tools/{tool['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/tools/{tool['id']}", headers=auth_headers).status_code == 404


def test_builtin_tools_are_seeded_and_listed(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    builtin_names = {t["name"] for t in tools if t["tool_type"] == "builtin"}
    assert "read_file" in builtin_names
    assert "web_search" in builtin_names


def test_update_builtin_tool_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    builtin_id = _get_builtin_tool_id(client, auth_headers)

    response = client.patch(
        f"/api/v1/tools/{builtin_id}",
        json={"name": "hacked_name"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    unchanged = client.get(f"/api/v1/tools/{builtin_id}", headers=auth_headers).json()
    assert unchanged["name"] != "hacked_name"


def test_delete_builtin_tool_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    builtin_id = _get_builtin_tool_id(client, auth_headers)

    response = client.delete(f"/api/v1/tools/{builtin_id}", headers=auth_headers)

    assert response.status_code == 400
    assert client.get(f"/api/v1/tools/{builtin_id}", headers=auth_headers).status_code == 200


def test_save_tool_version_writes_source_and_records_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = _create_custom_tool(client, auth_headers)
    source = 'from agno.tools import tool\n\n\n@tool\ndef my_tool() -> str:\n    return "hi"\n'

    response = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": source},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 2  # version 1 is the empty file created alongside the tool
    assert body["source_code"] == source
    assert Path(tool["source_path"]).read_text() == source


def test_save_tool_version_increments_across_multiple_saves(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = _create_custom_tool(client, auth_headers)

    first = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "# v2"},
        headers=auth_headers,
    )
    second = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "# v3"},
        headers=auth_headers,
    )

    assert first.json()["version"] == 2
    assert second.json()["version"] == 3
    versions = client.get(f"/api/v1/tools/{tool['id']}/versions", headers=auth_headers).json()
    assert [v["version"] for v in versions] == [3, 2, 1]


def test_save_tool_version_rejects_invalid_python(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = _create_custom_tool(client, auth_headers)
    original_source = Path(tool["source_path"]).read_text()

    response = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "def broken(:\n"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert "Invalid Python" in response.json()["detail"]
    # Neither the file nor the version history changed.
    assert Path(tool["source_path"]).read_text() == original_source
    versions = client.get(f"/api/v1/tools/{tool['id']}/versions", headers=auth_headers).json()
    assert len(versions) == 1


def test_custom_and_default_builtin_tools_report_available(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    custom = _create_custom_tool(client, auth_headers)
    assert custom["available"] is True

    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    read_file = next(t for t in tools if t["name"] == "read_file")
    assert read_file["available"] is True


def test_execute_python_availability_reflects_sandbox_backend(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute_python is the one builtin tool with a real availability
    check (NFR-2.4's "unavailable" pattern) -- it's only functional if
    this machine has ADR-008's sandbox backend installed. Patching the
    router's own availability registry (rather than
    code_exec.is_available itself) matches how it's actually wired: the
    registry captures a function reference at import time, so patching
    the underlying function after that wouldn't be observed here."""
    monkeypatch.setitem(
        tools_api._BUILTIN_AVAILABILITY,  # pyright: ignore[reportPrivateUsage]
        "execute_python",
        lambda: False,
    )
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    assert next(t for t in tools if t["name"] == "execute_python")["available"] is False

    monkeypatch.setitem(
        tools_api._BUILTIN_AVAILABILITY,  # pyright: ignore[reportPrivateUsage]
        "execute_python",
        lambda: True,
    )
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    assert next(t for t in tools if t["name"] == "execute_python")["available"] is True

    single = client.get(
        f"/api/v1/tools/{next(t for t in tools if t['name'] == 'execute_python')['id']}",
        headers=auth_headers,
    )
    assert single.json()["available"] is True


def test_save_tool_version_rejected_for_builtin_tool(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    builtin_id = _get_builtin_tool_id(client, auth_headers)

    response = client.post(
        f"/api/v1/tools/{builtin_id}/versions",
        json={"source_code": "# irrelevant"},
        headers=auth_headers,
    )

    assert response.status_code == 400


def test_create_tool_simple_mode_is_not_yet_implemented(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/tools",
        json={"name": "codegen_tool", "description": "d", "mode": "simple", "prompt": "do a thing"},
        headers=auth_headers,
    )
    assert response.status_code == 501


def _create_custom_tool_named(
    client: TestClient, auth_headers: dict[str, str], name: str
) -> dict[str, Any]:
    """Like _create_custom_tool, but with a caller-chosen name -- needed for
    tests that make assertions about the file's exact on-disk content,
    since "my_tool" (the fixed name _create_custom_tool always uses) maps
    to the same tools_dir path across every test in this session (a real
    file on disk, not reset per-test the way the in-memory DB is)."""
    create = client.post(
        "/api/v1/tools",
        json={"name": name, "description": "Does a thing."},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    return create.json()


def test_rollback_tool_version_restores_earlier_source(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """create_tool records version 1 as an empty ToolVersion row without
    writing anything to disk yet (the file only gets created by the first
    real save) -- so version 2 has to actually exist on disk before this
    test can meaningfully assert that rolling back to version 1 restores
    the (empty) pre-save content."""
    tool = _create_custom_tool_named(client, auth_headers, "rollback_test_tool")

    client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "# v2 content"},
        headers=auth_headers,
    )
    assert Path(tool["source_path"]).read_text() == "# v2 content"

    rollback = client.post(f"/api/v1/tools/{tool['id']}/versions/1/rollback", headers=auth_headers)
    assert rollback.status_code == 200, rollback.text
    assert Path(tool["source_path"]).read_text() == ""


def test_rollback_tool_version_returns_404_for_unknown_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = _create_custom_tool_named(client, auth_headers, "rollback_404_test_tool")

    response = client.post(
        f"/api/v1/tools/{tool['id']}/versions/999/rollback", headers=auth_headers
    )
    assert response.status_code == 404


def test_open_tool_editor_returns_the_source_path(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = _create_custom_tool_named(client, auth_headers, "open_editor_test_tool")

    response = client.post(f"/api/v1/tools/{tool['id']}/open-editor", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"path": tool["source_path"]}


def test_open_tool_editor_rejects_a_tool_with_no_source_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Builtin tool rows never get a source_path (api/tools.py's ToolOut).
    builtin_id = _get_builtin_tool_id(client, auth_headers)

    response = client.post(f"/api/v1/tools/{builtin_id}/open-editor", headers=auth_headers)

    assert response.status_code == 400


def test_list_tool_scopes_returns_the_known_catalog(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#188: the fixed scope catalog an owner can grant to an agent via
    PUT /agents/{agent_id}/tool-scopes."""
    response = client.get("/api/v1/tools/scopes", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json() == [
        "agents_teams:manage",
        "channels:manage",
        "invites:manage",
        "mcp_servers:manage",
        "settings:manage",
        "workflows:manage",
    ]


def test_list_tools_exposes_required_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # #189 is the first real consumer of #188's mechanism: the five
    # mutating channel tools require "channels:manage"; everything else,
    # including the read-only list_channels, still reports None.
    tools = {
        tool["name"]: tool["required_scope"]
        for tool in client.get("/api/v1/tools", headers=auth_headers).json()
    }
    assert tools
    assert tools["create_channel"] == "channels:manage"
    assert tools["update_channel"] == "channels:manage"
    assert tools["archive_channel"] == "channels:manage"
    assert tools["unarchive_channel"] == "channels:manage"
    assert tools["reorder_channels"] == "channels:manage"
    assert tools["list_channels"] is None
    assert tools["read_file"] is None
    # #190: the nine mutating agent/team tools require "agents_teams:manage";
    # list_agents/list_teams, like list_channels above, report None.
    assert tools["create_agent"] == "agents_teams:manage"
    assert tools["update_agent"] == "agents_teams:manage"
    assert tools["delete_agent"] == "agents_teams:manage"
    assert tools["update_agent_routing_rules"] == "agents_teams:manage"
    assert tools["update_agent_peer_preference"] == "agents_teams:manage"
    assert tools["rollback_agent_version"] == "agents_teams:manage"
    assert tools["create_team"] == "agents_teams:manage"
    assert tools["update_team"] == "agents_teams:manage"
    assert tools["delete_team"] == "agents_teams:manage"
    assert tools["list_agents"] is None
    assert tools["list_teams"] is None
    # #191: the three mutating MCP server tools require "mcp_servers:manage";
    # list_mcp_servers, like list_channels/list_agents above, reports None.
    assert tools["register_mcp_server"] == "mcp_servers:manage"
    assert tools["reconnect_mcp_server"] == "mcp_servers:manage"
    assert tools["delete_mcp_server"] == "mcp_servers:manage"
    assert tools["list_mcp_servers"] is None
