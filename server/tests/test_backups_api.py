"""api/backups.py — manual backup, listing, and restore over HTTP.

Automatic (daily/pre-upgrade) snapshots are covered by test_backup.py and
are no-op'd for the `client` fixture (see conftest.py) — this file only
exercises what a user triggers directly.
"""

from fastapi.testclient import TestClient


def test_create_manual_backup_returns_metadata(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/backups", headers=auth_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "manual"
    assert body["filename"].startswith("manual-")
    assert body["size_bytes"] > 0
    assert body["created_at"]


def test_list_backups_includes_created_manual_backup(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/backups", headers=auth_headers).json()

    response = client.get("/api/v1/backups", headers=auth_headers)

    assert response.status_code == 200
    filenames = {b["filename"] for b in response.json()}
    assert created["filename"] in filenames


def test_backups_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/backups").status_code == 401
    assert client.post("/api/v1/backups").status_code == 401
    assert client.post("/api/v1/backups/whatever.db/restore").status_code == 401


def test_restore_unknown_backup_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/backups/does-not-exist.db/restore", headers=auth_headers)
    assert response.status_code == 404


def test_restore_rejects_path_outside_backups_dir(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # %2e%2e is a single non-"/" path segment at the routing layer (so it
    # actually reaches this handler, unlike a literal ".." which the HTTP
    # client normalizes away before the request is even sent) that decodes
    # to ".." once FastAPI hands it to the handler as `filename` — it
    # resolves back to backups_dir itself, a directory not a file, so
    # resolve_backup_path rejects it the same way an out-of-tree path would.
    response = client.post("/api/v1/backups/%2e%2e/restore", headers=auth_headers)
    assert response.status_code == 404


def test_restore_reverts_workspace_state_to_snapshot(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    backup = client.post("/api/v1/backups", headers=auth_headers).json()

    added = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-test"},
        headers=auth_headers,
    )
    assert added.status_code == 201, added.text
    assert len(client.get("/api/v1/providers", headers=auth_headers).json()) == 1

    restore = client.post(
        f"/api/v1/backups/{backup['filename']}/restore", headers=auth_headers
    )
    assert restore.status_code == 204, restore.text

    after = client.get("/api/v1/providers", headers=auth_headers)
    assert after.status_code == 200, after.text
    assert after.json() == []
