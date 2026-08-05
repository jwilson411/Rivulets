"""Tools API (FR-8.2 through FR-8.4) HTTP-layer tests.

Custom-tool CRUD is exercised end-to-end here; agent-side resolution of
the resulting Tool rows into real agno objects is covered separately in
test_tool_resolution.py. Builtin tool rows only exist because app.py's
lifespan calls seed_builtin_tools() at startup -- the `client` fixture
triggers that via create_app(), so a builtin row is always present.
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


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
