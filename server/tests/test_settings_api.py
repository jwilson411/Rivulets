"""api/settings.py -- workspace settings get/patch. Only exercised
elsewhere in the suite as setup for a sync-engine-offline check
(test_sync.py's test_settings_patch_does_not_fail_when_sync_engine_not_running),
never GET, the unknown-key rejection, or updating an already-stored key."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rivulets.config import get_settings


def test_get_settings_returns_defaults_when_nothing_is_stored(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/settings", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["guard.turn_limit"] == 10
    assert body["ui.port"] == 8484


def test_patch_settings_rejects_unknown_key(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/v1/settings", json={"not.a.real.setting": True}, headers=auth_headers
    )
    assert response.status_code == 400


def test_patch_settings_updates_an_already_stored_key(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first = client.patch("/api/v1/settings", json={"guard.turn_limit": 5}, headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["guard.turn_limit"] == 5

    second = client.patch("/api/v1/settings", json={"guard.turn_limit": 7}, headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["guard.turn_limit"] == 7


def test_patch_settings_excludes_ui_port_from_sync(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ui.port is this node's own local preference, not workspace policy
    (api/settings.py's module docstring) -- publish_current_state must
    never be called for it, only for genuinely-synced keys."""
    published: list[str] = []

    async def fake_publish(_db: object, _entity_type: str, entity_id: str) -> None:
        published.append(entity_id)

    monkeypatch.setattr("rivulets.api.settings.publish_current_state", fake_publish)

    response = client.patch(
        "/api/v1/settings",
        json={"ui.port": 9000, "guard.turn_limit": 3},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert published == ["guard.turn_limit"]


def test_get_settings_includes_working_directory_default(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/settings", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["tools.working_directory"] is None


def test_patch_working_directory_stores_resolved_path(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    response = client.patch(
        "/api/v1/settings",
        json={"tools.working_directory": str(project)},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["tools.working_directory"] == str(project.resolve())


def test_patch_working_directory_rejects_missing_folder(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    response = client.patch(
        "/api/v1/settings",
        json={"tools.working_directory": str(tmp_path / "nope")},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "not a folder" in response.json()["detail"]


def test_patch_working_directory_rejects_rivulets_data_dir(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workspace = get_settings().workspace_dir
    response = client.patch(
        "/api/v1/settings",
        json={"tools.working_directory": str(workspace)},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "own data directory" in response.json()["detail"]


def test_patch_working_directory_clears_with_null(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    client.patch(
        "/api/v1/settings",
        json={"tools.working_directory": str(project)},
        headers=auth_headers,
    )
    response = client.patch(
        "/api/v1/settings",
        json={"tools.working_directory": None},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["tools.working_directory"] is None


def test_patch_working_directory_is_not_synced(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    published: list[str] = []

    async def fake_publish(_db: object, _entity_type: str, entity_id: str) -> None:
        published.append(entity_id)

    monkeypatch.setattr("rivulets.api.settings.publish_current_state", fake_publish)

    response = client.patch(
        "/api/v1/settings",
        json={"tools.working_directory": str(project), "guard.turn_limit": 3},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert published == ["guard.turn_limit"]


def test_list_directories_returns_child_folders(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "hidden").mkdir()
    (tmp_path / ".dot").mkdir()
    (tmp_path / "file.txt").write_text("x")

    response = client.get(
        "/api/v1/settings/directories",
        params={"path": str(tmp_path)},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(tmp_path.resolve())
    assert [entry["name"] for entry in body["entries"]] == ["alpha", "beta", "hidden"]


def test_list_directories_rejects_a_file(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x")
    response = client.get(
        "/api/v1/settings/directories",
        params={"path": str(file_path)},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_create_directory_makes_a_child_folder(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    response = client.post(
        "/api/v1/settings/directories",
        json={"parent": str(tmp_path), "name": "new-app"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert (tmp_path / "new-app").is_dir()
    assert "new-app" in [entry["name"] for entry in response.json()["entries"]]


def test_create_directory_rejects_path_separators(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    response = client.post(
        "/api/v1/settings/directories",
        json={"parent": str(tmp_path), "name": "../escape"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert not (tmp_path.parent / "escape").exists()


def test_directories_require_auth(client: TestClient) -> None:
    response = client.get("/api/v1/settings/directories")
    assert response.status_code == 401
