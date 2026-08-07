"""api/update.py -- GET /update/status and POST /update/apply.

apply_update() and the background exit are always monkeypatched here:
letting the real _exit_after_response run inside the TestClient (which
executes BackgroundTasks synchronously before .post() returns) would call
os._exit(0) and kill the pytest process itself.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from rivulets.api import update as update_api
from rivulets.update import (
    UpdateNotApplicableError,
    UpdateNotAvailableError,
    UpdateStatus,
    UpdateVerificationError,
)


def _status(**overrides: Any) -> UpdateStatus:
    defaults: dict[str, Any] = {
        "current_version": "0.1.0",
        "latest_version": None,
        "update_available": False,
        "applicable": False,
        "asset_name": None,
        "download_url": None,
        "checksum_url": None,
    }
    defaults.update(overrides)
    return UpdateStatus(**defaults)


def test_status_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/update/status").status_code == 401


def test_apply_requires_auth(client: TestClient) -> None:
    assert client.post("/api/v1/update/apply").status_code == 401


def test_status_reports_no_update(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_check() -> UpdateStatus:
        return _status()

    monkeypatch.setattr(update_api, "check_for_update", fake_check)

    response = client.get("/api/v1/update/status", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "current_version": "0.1.0",
        "latest_version": None,
        "update_available": False,
        "applicable": False,
    }


def test_status_reports_available_update(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_check() -> UpdateStatus:
        return _status(
            latest_version="v0.2.0",
            update_available=True,
            applicable=True,
            asset_name="rivulets-linux-amd64",
            download_url="https://example.com/bin",
            checksum_url="https://example.com/bin.sha256",
        )

    monkeypatch.setattr(update_api, "check_for_update", fake_check)

    response = client.get("/api/v1/update/status", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["latest_version"] == "v0.2.0"
    assert body["update_available"] is True
    assert body["applicable"] is True
    # UpdateStatusOut deliberately doesn't leak the asset/download URLs to
    # the UI -- apply's the only thing that needs them, server-side.
    assert "download_url" not in body


def test_apply_succeeds_and_schedules_restart(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exited = []

    async def fake_apply() -> None:
        return None

    monkeypatch.setattr(update_api, "apply_update", fake_apply)
    monkeypatch.setattr(update_api, "_exit_after_response", lambda: exited.append(True))

    response = client.post("/api/v1/update/apply", headers=auth_headers)

    assert response.status_code == 202
    assert response.json() == {"status": "restarting"}
    assert exited == [True]


def test_apply_not_applicable_returns_409(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_apply() -> None:
        raise UpdateNotApplicableError("not a packaged binary")

    monkeypatch.setattr(update_api, "apply_update", fake_apply)
    monkeypatch.setattr(update_api, "_exit_after_response", lambda: None)

    response = client.post("/api/v1/update/apply", headers=auth_headers)

    assert response.status_code == 409
    assert "not a packaged binary" in response.json()["detail"]


def test_apply_no_update_available_returns_409(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_apply() -> None:
        raise UpdateNotAvailableError("No update available.")

    monkeypatch.setattr(update_api, "apply_update", fake_apply)
    monkeypatch.setattr(update_api, "_exit_after_response", lambda: None)

    response = client.post("/api/v1/update/apply", headers=auth_headers)

    assert response.status_code == 409


def test_apply_verification_failure_returns_502(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_apply() -> None:
        raise UpdateVerificationError("checksum mismatch")

    monkeypatch.setattr(update_api, "apply_update", fake_apply)
    monkeypatch.setattr(update_api, "_exit_after_response", lambda: None)

    response = client.post("/api/v1/update/apply", headers=auth_headers)

    assert response.status_code == 502


def test_apply_os_error_returns_500(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_apply() -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(update_api, "apply_update", fake_apply)
    monkeypatch.setattr(update_api, "_exit_after_response", lambda: None)

    response = client.post("/api/v1/update/apply", headers=auth_headers)

    assert response.status_code == 500
