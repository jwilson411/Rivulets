"""Owner-only Google integration accounts (api/integrations.py, #458)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from rivulets.integrations import google as google_mod
from rivulets.integrations.registry import get_connected_account, list_connected_accounts
from rivulets.integrations.tokens import load_tokens
from rivulets.security.credentials import CredentialStoreError

_RealClient = httpx.Client


def _mock_client(handler: object) -> object:
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        **kwargs,
    )


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def test_list_integrations_starts_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/integrations", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_oauth_app_starts_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/integrations/google/oauth-app", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "google"
    assert body["client_id"] == ""
    assert body["has_client_secret"] is False
    assert body["redirect_uri"].startswith("http://127.0.0.1:")
    assert body["redirect_uri"].endswith("/api/v1/integrations/google/callback")
    assert "gmail_read" in body["default_capabilities"]
    assert "gmail_write" not in body["default_capabilities"]
    writes = [item for item in body["capabilities"] if item["id"] == "gmail_write"]
    assert writes and writes[0]["write"] is True


def test_put_oauth_app_never_returns_secret(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123", "client_secret": "super-secret"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["client_id"] == "client-123"
    assert body["has_client_secret"] is True
    assert "super-secret" not in response.text
    assert "client_secret" not in body


def test_put_oauth_app_can_clear_secret(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123", "client_secret": "super-secret"},
        headers=auth_headers,
    )
    cleared = client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123", "client_secret": ""},
        headers=auth_headers,
    )
    assert cleared.json()["has_client_secret"] is False


def test_connect_requires_oauth_client(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/integrations/google/connect", json={}, headers=auth_headers)
    assert response.status_code == 400
    assert "OAuth client ID" in response.json()["detail"]


def test_connect_returns_google_authorization_url(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    response = client.post(
        "/api/v1/integrations/google/connect",
        json={"label": "Work"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    url = response.json()["authorization_url"]
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["client-123"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert "state" in query
    assert "gmail.readonly" in query["scope"][0]
    assert "gmail.send" not in query["scope"][0]
    assert "drive.readonly" in query["scope"][0]
    assert "documents.readonly" in query["scope"][0]
    assert "spreadsheets.readonly" in query["scope"][0]
    assert "contacts.readonly" in query["scope"][0]
    assert "tasks.readonly" in query["scope"][0]
    assert "meetings.space.created" not in query["scope"][0]


def test_connect_requests_only_selected_capabilities(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    response = client.post(
        "/api/v1/integrations/google/connect",
        json={"capabilities": ["gmail_write"]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    scope = parse_qs(urlparse(response.json()["authorization_url"]).query)["scope"][0]
    assert "gmail.send" in scope
    assert "gmail.compose" in scope
    assert "gmail.readonly" in scope
    assert "drive" not in scope
    assert "calendar" not in scope


def test_connect_rejects_unknown_or_empty_capabilities(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    unknown = client.post(
        "/api/v1/integrations/google/connect",
        json={"capabilities": ["not_a_surface"]},
        headers=auth_headers,
    )
    assert unknown.status_code == 422
    empty = client.post(
        "/api/v1/integrations/google/connect",
        json={"capabilities": []},
        headers=auth_headers,
    )
    assert empty.status_code == 422


def test_invite_grant_cannot_connect_or_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invite_headers = _invite_headers(client, auth_headers)
    assert client.get("/api/v1/integrations", headers=invite_headers).status_code == 403
    assert (
        client.get("/api/v1/integrations/google/oauth-app", headers=invite_headers).status_code
        == 403
    )
    assert (
        client.put(
            "/api/v1/integrations/google/oauth-app",
            json={"client_id": "stolen"},
            headers=invite_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/integrations/google/connect", json={}, headers=invite_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/integrations/missing/reconnect", json={}, headers=invite_headers
        ).status_code
        == 403
    )


def _fake_token_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "ya29.access",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly email",
            "token_type": "Bearer",
        },
    )


def _fake_userinfo(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"email": "ada@example.com"})


def test_callback_stores_token_outside_workspace_db(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    connect = client.post(
        "/api/v1/integrations/google/connect",
        json={"label": "Work"},
        headers=auth_headers,
    )
    state = parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _fake_token_response(request)
        if request.url.host == "www.googleapis.com":
            return _fake_userinfo(request)
        return httpx.Response(404, text="unexpected")

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client(handler))

    callback = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
    )
    assert callback.status_code == 200, callback.text
    assert "ada@example.com" in callback.text
    assert "/settings?tab=integrations" in callback.text
    assert "location.replace('/settings?tab=integrations')" in callback.text
    assert "ya29.access" not in callback.text
    assert "1//refresh" not in callback.text

    listed = client.get("/api/v1/integrations", headers=auth_headers)
    assert listed.status_code == 200
    accounts = listed.json()
    assert len(accounts) == 1
    assert accounts[0]["account_email"] == "ada@example.com"
    assert accounts[0]["status"] == "connected"
    assert accounts[0]["capabilities"] == ["gmail_read"]
    assert "credential_ref" not in accounts[0]
    assert "ya29.access" not in listed.text

    connected = get_connected_account("google")
    assert connected is not None
    assert connected.id == accounts[0]["id"]
    tokens = load_tokens(connected.credential_ref)
    assert tokens.access_token == "ya29.access"  # noqa: S105
    assert tokens.refresh_token == "1//refresh"  # noqa: S105
    assert "ya29" not in connected.credential_ref


def test_callback_rejects_unknown_state(client: TestClient) -> None:
    response = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "x", "state": "nope"},
    )
    assert response.status_code == 400
    assert "invalid or has expired" in response.text
    assert 'href="/settings?tab=integrations"' in response.text
    assert "location.replace" not in response.text


def test_disconnect_revokes_and_fails_closed(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    connect = client.post("/api/v1/integrations/google/connect", json={}, headers=auth_headers)
    state = parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"][0]
    revoked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(google_mod.TOKEN_ENDPOINT):
            return _fake_token_response(request)
        if str(request.url).startswith(google_mod.USERINFO_ENDPOINT):
            return _fake_userinfo(request)
        if str(request.url).startswith(google_mod.REVOKE_ENDPOINT):
            revoked.append(request.content.decode())
            return httpx.Response(200, text="ok")
        return httpx.Response(404, text="unexpected")

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client(handler))
    client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
    )
    account_id = client.get("/api/v1/integrations", headers=auth_headers).json()[0]["id"]
    connected = get_connected_account("google")
    assert connected is not None
    ref = connected.credential_ref

    deleted = client.delete(f"/api/v1/integrations/{account_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert revoked
    assert list_connected_accounts("google") == []
    assert client.get("/api/v1/integrations", headers=auth_headers).json() == []
    with pytest.raises(CredentialStoreError):
        load_tokens(ref)


def test_invite_grant_cannot_disconnect(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    connect = client.post("/api/v1/integrations/google/connect", json={}, headers=auth_headers)
    state = parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _fake_token_response(request)
        return _fake_userinfo(request)

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client(handler))
    client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
    )
    account_id = client.get("/api/v1/integrations", headers=auth_headers).json()[0]["id"]
    invite_headers = _invite_headers(client, auth_headers)
    assert (
        client.delete(f"/api/v1/integrations/{account_id}", headers=invite_headers).status_code
        == 403
    )
    assert len(client.get("/api/v1/integrations", headers=auth_headers).json()) == 1


def _complete_google_connect(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    label: str = "Work",
    account_id: str | None = None,
    access_token: str = "ya29.access",  # noqa: S107
    refresh_token: str = "1//refresh",  # noqa: S107
    email: str = "ada@example.com",
    scope: str = "https://www.googleapis.com/auth/gmail.readonly email",
) -> str:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    if account_id is None:
        connect = client.post(
            "/api/v1/integrations/google/connect",
            json={"label": label},
            headers=auth_headers,
        )
    else:
        connect = client.post(
            f"/api/v1/integrations/{account_id}/reconnect",
            json={},
            headers=auth_headers,
        )
    assert connect.status_code == 200, connect.text
    state = parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(google_mod.TOKEN_ENDPOINT):
            return httpx.Response(
                200,
                json={
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": 3600,
                    "scope": scope,
                    "token_type": "Bearer",
                },
            )
        if str(request.url).startswith(google_mod.USERINFO_ENDPOINT):
            return httpx.Response(200, json={"email": email})
        return httpx.Response(404, text="unexpected")

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client(handler))
    callback = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code", "state": state},
    )
    assert callback.status_code == 200, callback.text
    listed = client.get("/api/v1/integrations", headers=auth_headers).json()
    assert listed
    return listed[0]["id"] if account_id is None else account_id


def test_reconnect_keeps_account_id_and_replaces_tokens(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _complete_google_connect(client, auth_headers, monkeypatch)
    first = get_connected_account("google")
    assert first is not None
    assert first.id == account_id
    assert load_tokens(first.credential_ref).access_token == "ya29.access"  # noqa: S105

    reconnect = client.post(
        f"/api/v1/integrations/{account_id}/reconnect",
        json={"capabilities": ["gmail_write", "contacts_read"]},
        headers=auth_headers,
    )
    assert reconnect.status_code == 200
    query = parse_qs(urlparse(reconnect.json()["authorization_url"]).query)
    assert query["login_hint"] == ["ada@example.com"]
    assert "gmail.send" in query["scope"][0]
    assert "contacts.readonly" in query["scope"][0]
    assert "drive" not in query["scope"][0]
    state = query["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(google_mod.TOKEN_ENDPOINT):
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.rotated",
                    "expires_in": 3600,
                    "scope": (
                        "https://www.googleapis.com/auth/gmail.readonly "
                        "https://www.googleapis.com/auth/contacts.readonly email"
                    ),
                    "token_type": "Bearer",
                },
            )
        if str(request.url).startswith(google_mod.USERINFO_ENDPOINT):
            return httpx.Response(200, json={"email": "ada@example.com"})
        return httpx.Response(404, text="unexpected")

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client(handler))
    callback = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "auth-code-2", "state": state},
    )
    assert callback.status_code == 200, callback.text
    assert "reconnected" in callback.text
    listed = client.get("/api/v1/integrations", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["id"] == account_id
    assert any("contacts.readonly" in scope for scope in listed[0]["scopes"])
    connected = get_connected_account("google")
    assert connected is not None
    assert connected.id == account_id
    tokens = load_tokens(connected.credential_ref)
    assert tokens.access_token == "ya29.rotated"  # noqa: S105
    assert tokens.refresh_token == "1//refresh"  # noqa: S105


def test_second_connect_same_email_updates_existing_account(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _complete_google_connect(client, auth_headers, monkeypatch)
    _complete_google_connect(
        client,
        auth_headers,
        monkeypatch,
        access_token="ya29.again",  # noqa: S106
        refresh_token="1//new-refresh",  # noqa: S106
        scope="https://www.googleapis.com/auth/tasks.readonly email",
    )
    listed = client.get("/api/v1/integrations", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["id"] == account_id
    assert any("tasks.readonly" in scope for scope in listed[0]["scopes"])
    connected = get_connected_account("google")
    assert connected is not None
    assert load_tokens(connected.credential_ref).access_token == "ya29.again"  # noqa: S105


def test_reconnect_missing_account_is_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.put(
        "/api/v1/integrations/google/oauth-app",
        json={"client_id": "client-123"},
        headers=auth_headers,
    )
    response = client.post("/api/v1/integrations/missing/reconnect", json={}, headers=auth_headers)
    assert response.status_code == 404


def test_invite_grant_cannot_reconnect(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _complete_google_connect(client, auth_headers, monkeypatch)
    invite_headers = _invite_headers(client, auth_headers)
    assert (
        client.post(
            f"/api/v1/integrations/{account_id}/reconnect",
            json={},
            headers=invite_headers,
        ).status_code
        == 403
    )
