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
from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope


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


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


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


async def test_delete_custom_tool_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#287: the `client` fixture never actually starts the sync engine, so
    a successful delete queues a tombstone retry (SyncPendingOutbound.
    deleted=True) instead of the delete never reaching any peer at all --
    mirrors test_teams_api.py's equivalent for delete_team."""
    tool = _create_custom_tool(client, auth_headers)

    deleted = client.delete(f"/api/v1/tools/{tool['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("tool", tool["id"]))
        assert pending is not None
        assert pending.deleted is True


def test_delete_custom_tool_unlinks_source_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#362: the source file is where operators bake integration secrets
    (see list_tool_versions' docstring) -- deleting the tool must remove
    it, not leave executable source orphaned on disk."""
    tool = _create_custom_tool(client, auth_headers)
    save = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "SECRET = 'hunter2'\n"},
        headers=auth_headers,
    )
    assert save.status_code == 201, save.text
    assert Path(tool["source_path"]).exists()

    deleted = client.delete(f"/api/v1/tools/{tool['id']}", headers=auth_headers)

    assert deleted.status_code == 204
    assert not Path(tool["source_path"]).exists()


def test_save_and_rollback_and_delete_resync_agentos(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#362: custom tool source is loaded at agent *build* time
    (agentos/tool_resolution.py), so every route that changes what's on
    disk (save/rollback) or removes the tool outright (delete) must
    rebuild the registry, or agents keep executing the previous source
    from memory. Counts calls to the module-global sync_agents the routes
    resolve at call time."""
    calls: list[None] = []

    async def fake_sync_agents(db: object) -> None:
        calls.append(None)

    monkeypatch.setattr(tools_api, "sync_agents", fake_sync_agents)
    tool = _create_custom_tool(client, auth_headers)

    save = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "def my_tool() -> str:\n    return 'v2'\n"},
        headers=auth_headers,
    )
    assert save.status_code == 201, save.text
    assert len(calls) == 1

    rollback = client.post(f"/api/v1/tools/{tool['id']}/versions/1/rollback", headers=auth_headers)
    assert rollback.status_code == 200, rollback.text
    assert len(calls) == 2

    deleted = client.delete(f"/api/v1/tools/{tool['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert len(calls) == 3


async def test_delete_builtin_tool_does_not_queue_a_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Deleting a builtin tool is rejected outright (test_delete_builtin_
    tool_is_rejected above); this covers the other non-tombstoned case --
    an 'mcp' tool_type isn't exercised here since it always comes from
    mcp_servers.py's discovery flow, not a client-supplied create."""
    builtin_id = _get_builtin_tool_id(client, auth_headers)

    client.delete(f"/api/v1/tools/{builtin_id}", headers=auth_headers)

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("tool", builtin_id))
        assert pending is None


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
    # create_tool doesn't write to disk until the first real save (module
    # docstring / test_custom_tool_write_routes_require_owner_grant), so a
    # freshly-created (id-keyed, #289) tool has no file yet to compare
    # against here -- the assertion below is just "still doesn't exist".
    assert not Path(tool["source_path"]).exists()

    response = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "def broken(:\n"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert "Invalid Python" in response.json()["detail"]
    # Neither the file nor the version history changed.
    assert not Path(tool["source_path"]).exists()
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
        "sensitive_tools:manage",
        "settings:manage",
        "workflows:manage",
    ]


def test_custom_tool_write_routes_require_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Custom tool source is arbitrary Python that _load_custom_tool
    (agentos/tool_resolution.py) execs directly in the app-server process,
    unsandboxed, once the tool is assigned to and run by an agent -- the
    same "sensitive surface" bucket as provider credentials/backups/sync/
    settings (api/deps.py's require_owner_grant). An invite-grant session
    must never be able to create, edit, or delete a tool's code."""
    tool = _create_custom_tool_named(client, auth_headers, "owner_gate_test_tool")
    invite_headers = _invite_headers(client, auth_headers)

    assert (
        client.post(
            "/api/v1/tools",
            json={"name": "invite_created_tool", "description": "d"},
            headers=invite_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/tools/{tool['id']}", json={"name": "renamed"}, headers=invite_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/tools/{tool['id']}/versions",
            json={"source_code": "# malicious"},
            headers=invite_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/tools/{tool['id']}/versions/1/rollback", headers=invite_headers
        ).status_code
        == 403
    )
    assert (
        client.post(f"/api/v1/tools/{tool['id']}/open-editor", headers=invite_headers).status_code
        == 403
    )
    assert client.delete(f"/api/v1/tools/{tool['id']}", headers=invite_headers).status_code == 403

    # The tool's source file was never created/touched by any of the above
    # (create_tool doesn't write to disk until the first real save -- see
    # test_rollback_tool_version_restores_earlier_source's docstring).
    assert not Path(tool["source_path"]).exists()


def test_list_tool_versions_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#321: list_tool_versions returns ToolVersionOut.source_code -- the
    same secrets-bearing payload the write routes above are gated to keep
    away from invite-grant. Reading every version's source is just as much
    a leak as writing it, so this needs the same OwnerGrant."""
    tool = _create_custom_tool_named(client, auth_headers, "read_gate_test_tool")
    client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "SECRET_WEBHOOK_URL = 'https://example.com/hook?token=shh'"},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.get(f"/api/v1/tools/{tool['id']}/versions", headers=invite_headers)

    assert response.status_code == 403
    assert "SECRET_WEBHOOK_URL" not in response.text


def test_create_tool_rejects_names_that_would_escape_tools_dir(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/tools",
        json={"name": "../../etc/evil", "description": "d"},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_create_tool_rejects_duplicate_custom_name(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#289: two custom tools with the same name would both resolve to
    `{name}.py` under the old path scheme, so whichever was created second
    would silently overwrite the first's source file the moment it was
    ever saved."""
    _create_custom_tool(client, auth_headers)
    dup = client.post(
        "/api/v1/tools",
        json={"name": "my_tool", "description": "A different tool, same name."},
        headers=auth_headers,
    )
    assert dup.status_code == 409


def test_update_tool_rejects_rename_onto_an_existing_custom_name(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create_custom_tool(client, auth_headers)
    other = client.post(
        "/api/v1/tools",
        json={"name": "other_tool", "description": "d"},
        headers=auth_headers,
    ).json()

    renamed = client.patch(
        f"/api/v1/tools/{other['id']}",
        json={"name": "my_tool"},
        headers=auth_headers,
    )
    assert renamed.status_code == 409


def test_create_tool_two_different_ids_get_two_different_source_files(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#289: source_path is keyed off the tool's own id, not its name, so
    renaming one custom tool to match another's original name (after the
    other is deleted, freeing the name) can never collide with a still-
    live tool's file on disk."""
    first = _create_custom_tool(client, auth_headers)
    assert client.delete(f"/api/v1/tools/{first['id']}", headers=auth_headers).status_code == 204

    second = client.post(
        "/api/v1/tools",
        json={"name": "my_tool", "description": "Reuses the freed name."},
        headers=auth_headers,
    ).json()
    assert second["id"] != first["id"]
    assert second["source_path"] != first["source_path"]
    assert Path(second["source_path"]).name == f"{second['id']}.py"


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


def test_list_tools_exposes_display_name_and_group(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#422: the agent Tools picker needs a human label and a group, not
    just the snake_case identifier. Custom tools land in their own group."""
    created = _create_custom_tool(client, auth_headers)
    listed = client.get("/api/v1/tools", headers=auth_headers).json()
    tools = {tool["name"]: tool for tool in listed}

    assert tools["http_request"]["display_name"] == "HTTP request"
    assert tools["http_request"]["group"] == "chat"
    assert tools["web_search"]["display_name"] == "Web search"
    assert tools["web_search"]["group"] == "chat"
    assert tools["fetch_webpage"]["display_name"] == "Fetch webpage"
    assert tools["fetch_webpage"]["group"] == "chat"
    assert tools["execute_python"]["display_name"] == "Execute Python"
    assert tools["execute_python"]["group"] == "files"
    assert tools["update_agent_peer_preference"]["display_name"] == "Update agent peer preference"
    assert tools["update_agent_peer_preference"]["group"] == "workspace_admin"
    assert tools["query_workspace_db"]["display_name"] == "Query workspace DB"
    assert tools[created["name"]]["display_name"] == "My tool"
    assert tools[created["name"]]["group"] == "custom"
    assert tools["http_request"]["description"]
