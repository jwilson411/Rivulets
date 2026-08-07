"""api/settings.py -- workspace settings get/patch. Only exercised
elsewhere in the suite as setup for a sync-engine-offline check
(test_sync.py's test_settings_patch_does_not_fail_when_sync_engine_not_running),
never GET, the unknown-key rejection, or updating an already-stored key."""

import pytest
from fastapi.testclient import TestClient


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
