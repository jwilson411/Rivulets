from fastapi.testclient import TestClient


def test_channel_crud_lifecycle(client: TestClient, auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/v1/channels",
        json={"name": "general", "description": "General discussion"},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    channel = create.json()
    assert channel["name"] == "general"
    assert channel["archived"] is False

    listed = client.get("/api/v1/channels", headers=auth_headers)
    assert len(listed.json()) == 1

    renamed = client.patch(
        f"/api/v1/channels/{channel['id']}",
        json={"name": "general-discussion"},
        headers=auth_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "general-discussion"

    archived = client.delete(f"/api/v1/channels/{channel['id']}", headers=auth_headers)
    assert archived.status_code == 204

    active_only = client.get("/api/v1/channels", headers=auth_headers)
    assert all(c["archived"] for c in active_only.json() if c["id"] == channel["id"])

    restored = client.post(f"/api/v1/channels/{channel['id']}/unarchive", headers=auth_headers)
    assert restored.status_code == 200
    assert restored.json()["archived"] is False


def test_channel_name_length_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/channels", json={"name": "ab"}, headers=auth_headers)
    assert response.status_code == 400


def test_duplicate_active_channel_name_returns_409_via_global_handler(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """create_channel has no pre-check of its own (unlike agents/workflows)
    -- this exercises app.py's #250 IntegrityError safety net against
    Channel's real partial unique index (idx_channel_name, archived = 0)
    instead of a contrived exception."""
    first = client.post("/api/v1/channels", json={"name": "duplicate-name"}, headers=auth_headers)
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/channels", json={"name": "duplicate-name"}, headers=auth_headers)
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["error"]["code"] == "conflict"
    assert "sqlite" not in second.text.lower()
    assert "INSERT INTO" not in second.text


def test_reorder_channels(client: TestClient, auth_headers: dict[str, str]) -> None:
    ids = []
    for name in ("alpha-chan", "beta-chan", "gamma-chan"):
        created = client.post("/api/v1/channels", json={"name": name}, headers=auth_headers)
        ids.append(created.json()["id"])

    reordered = client.patch(
        "/api/v1/channels/reorder",
        json={"order": list(reversed(ids))},
        headers=auth_headers,
    )
    assert reordered.status_code == 204

    listed = client.get("/api/v1/channels", headers=auth_headers).json()
    assert [c["id"] for c in listed] == list(reversed(ids))
