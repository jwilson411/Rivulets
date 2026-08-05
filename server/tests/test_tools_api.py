"""Tools API (FR-8.2 through FR-8.4) HTTP-layer tests.

Custom-tool CRUD is exercised end-to-end here; agent-side resolution of
the resulting Tool rows into real agno objects is covered separately in
test_tool_resolution.py. Builtin tool rows only exist because app.py's
lifespan calls seed_builtin_tools() at startup -- the `client` fixture
triggers that via create_app(), so a builtin row is always present.
"""

from fastapi.testclient import TestClient


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
