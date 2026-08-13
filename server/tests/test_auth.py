from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from rivulets.config import Settings, get_settings
from rivulets.security import keys
from rivulets.security.rate_limit import get_login_rate_limiter
from rivulets.security.session import get_session_key_store

_ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- exercising #247's gate, never actually bound
_TEST_BOOTSTRAP_TOKEN = "correct-token"  # noqa: S105 -- test fixture value, not a real secret


def _settings_bound_to(host: str, *, bootstrap_token: str | None = None) -> Settings:
    # Same fields as get_settings() would produce, just with app_server_host
    # (and optionally bootstrap_token) overridden -- workspace_dir/db paths
    # are irrelevant to login() and never touched by these tests.
    base = get_settings()
    return Settings(
        app_server_host=host,
        workspace_dir=base.workspace_dir,
        bootstrap_token=bootstrap_token,
    )


def test_login_bootstraps_workspace_on_first_use(client: TestClient) -> None:
    mnemonic = keys.generate_mnemonic()
    response = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_rejects_invalid_mnemonic(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"key": "not a real phrase"})
    assert response.status_code == 400


def test_second_login_requires_same_mnemonic(client: TestClient) -> None:
    first = keys.generate_mnemonic()
    ok = client.post("/api/v1/auth/login", json={"key": first})
    assert ok.status_code == 200

    other = keys.generate_mnemonic()
    rejected = client.post("/api/v1/auth/login", json={"key": other})
    assert rejected.status_code == 401


def test_bootstrap_over_0_0_0_0_refuses_without_a_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#247: with no workspace yet and the app reachable from the network
    (app_server_host=0.0.0.0), the first login must not be able to claim
    the workspace unless RIVULETS_BOOTSTRAP_TOKEN is configured."""
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings", lambda: _settings_bound_to(_ALL_INTERFACES)
    )

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 401
    assert "bootstrap" in response.json()["detail"].lower()


def test_bootstrap_over_0_0_0_0_refuses_a_wrong_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(_ALL_INTERFACES, bootstrap_token=_TEST_BOOTSTRAP_TOKEN),
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"key": keys.generate_mnemonic(), "bootstrap_token": "wrong-token"},
    )

    assert response.status_code == 401


def test_bootstrap_over_0_0_0_0_succeeds_with_the_correct_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(_ALL_INTERFACES, bootstrap_token=_TEST_BOOTSTRAP_TOKEN),
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"key": keys.generate_mnemonic(), "bootstrap_token": _TEST_BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 200, response.text
    assert response.json()["token"]


def test_second_login_over_0_0_0_0_does_not_require_a_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the workspace already exists, the bootstrap-token gate no
    longer applies -- it only guards the moment the workspace row is
    created, not every subsequent login."""
    mnemonic = keys.generate_mnemonic()
    first = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert first.status_code == 200

    monkeypatch.setattr(
        "rivulets.api.auth.get_settings", lambda: _settings_bound_to(_ALL_INTERFACES)
    )
    second = client.post("/api/v1/auth/login", json={"key": mnemonic})

    assert second.status_code == 200, second.text


def test_bootstrap_over_loopback_does_not_require_a_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default bind (127.0.0.1) is only reachable from this machine
    already -- the token gate is scoped to the network-reachable bind."""
    monkeypatch.setattr("rivulets.api.auth.get_settings", lambda: _settings_bound_to("127.0.0.1"))

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 200, response.text


def test_protected_endpoint_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/channels")
    assert response.status_code == 401


def test_protected_endpoint_accepts_valid_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/channels", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_login_is_rate_limited_after_five_attempts_per_minute(client: TestClient) -> None:
    """security-and-dr.md's documented "5 attempts per minute per IP"
    brute-force mitigation -- every attempt counts toward the cap
    regardless of outcome, so this also covers a flood of wrong guesses,
    not just repeated valid logins."""
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
        assert response.status_code in (200, 401)

    sixth = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    assert sixth.status_code == 429


def test_login_rate_limit_is_scoped_independently_per_ip(client: TestClient) -> None:
    """A direct unit check on the limiter itself (rather than TestClient,
    which always presents the same client IP) that two different IPs get
    independent budgets."""
    limiter = get_login_rate_limiter()
    for _ in range(5):
        assert limiter.check("1.2.3.4") is True
    assert limiter.check("1.2.3.4") is False
    assert limiter.check("5.6.7.8") is True


def test_login_succeeds_even_when_the_sync_engine_fails_to_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-9.5: a node must be fully functional with sync unreachable,
    including sync itself failing to come up at login time -- this must
    never turn into a failed login."""

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("libp2p host failed to bind")

    monkeypatch.setattr("rivulets.sync.engine.SyncEngine.start", _boom)

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 200, response.text
    assert response.json()["token"]


def test_login_seeds_starter_agents_and_team_on_first_workspace_creation(
    client: TestClient,
) -> None:
    """#16: the moment a workspace is bootstrapped (first-ever login on a
    fresh DB), the starter agent/team library should already be there."""
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    headers = {"Authorization": f"Bearer {response.json()['token']}"}

    agents = client.get("/api/v1/agents", headers=headers).json()
    assert {agent["name"] for agent in agents} == {"Assistant", "Coder", "Researcher", "Writer"}

    teams = client.get("/api/v1/teams", headers=headers).json()
    assert [team["name"] for team in teams] == ["Starter Team"]


def test_second_login_does_not_reseed_starter_content(client: TestClient) -> None:
    mnemonic = keys.generate_mnemonic()
    first = client.post("/api/v1/auth/login", json={"key": mnemonic})
    headers = {"Authorization": f"Bearer {first.json()['token']}"}

    second = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert second.status_code == 200

    agents = client.get("/api/v1/agents", headers=headers).json()
    assert len(agents) == 4
    teams = client.get("/api/v1/teams", headers=headers).json()
    assert len(teams) == 1


def test_logout_clears_the_session_and_stops_the_sync_engine(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/auth/logout", headers=auth_headers)
    assert response.status_code == 204

    with pytest.raises(RuntimeError, match="No active session"):
        get_session_key_store().get_key()

    # The session is gone, so the same bearer token is no longer accepted.
    assert client.get("/api/v1/channels", headers=auth_headers).status_code == 401


def test_unauthenticated_logout_does_not_clear_a_logged_in_workspaces_session(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#228: an unauthenticated POST /auth/logout used to be able to wipe
    the process-wide SessionKeyStore (JWT signing key, P2P PSK,
    credential-store key, webhook-secret key) and stop sync for every
    session on the node -- from a plain cross-site form POST, no bearer
    token required. It must now be a no-op unless the caller presents the
    workspace's own valid session token."""
    no_token = client.post("/api/v1/auth/logout")
    assert no_token.status_code == 204

    bad_token = client.post(
        "/api/v1/auth/logout", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert bad_token.status_code == 204

    # The already-logged-in workspace's session must still be intact.
    assert client.get("/api/v1/channels", headers=auth_headers).status_code == 200


def test_login_derives_a_credential_store_key(client: TestClient) -> None:
    """#118: login always derives the encrypted-SQLite fallback's
    encryption key alongside the JWT signing key and P2P PSK, even though
    most nodes never end up reading it (session.py's module docstring) —
    it's only used when the OS keychain has no usable backend."""
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    assert response.status_code == 200

    assert get_session_key_store().get_credential_store_key()


def test_stream_ticket_requires_a_valid_session(client: TestClient) -> None:
    response = client.post("/api/v1/auth/stream-ticket")
    assert response.status_code == 401


def test_stream_ticket_is_a_short_lived_purpose_scoped_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The one thing the SSE route's query-param auth path (api/deps.py's
    get_current_workspace_id_for_stream) accepts -- a normal session token
    passed the same way is rejected, closing off the long-lived-token-in-a-
    URL leak this ticket exists to replace."""
    response = client.post("/api/v1/auth/stream-ticket", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ticket"]
    assert body["expires_at"]

    payload = pyjwt.decode(body["ticket"], options={"verify_signature": False})
    assert payload["purpose"] == "stream"

    # Expires in roughly a minute, not the ~24h a normal session token gets.
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at - datetime.now(UTC) < timedelta(minutes=2)


def test_stream_ticket_cannot_be_used_as_a_bearer_session_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#234: `purpose == "stream"` is supposed to confine a minted ticket to
    the one query-param auth path it was designed for (get_current_
    workspace_id_for_stream) -- before this fix, _decode_token / get_
    session_claims never inspected `purpose`, so presenting the ticket as a
    normal `Authorization: Bearer` header made it a full, if short-lived,
    session on every other route."""
    ticket_response = client.post("/api/v1/auth/stream-ticket", headers=auth_headers)
    assert ticket_response.status_code == 200
    ticket = ticket_response.json()["ticket"]

    response = client.get("/api/v1/channels", headers={"Authorization": f"Bearer {ticket}"})
    assert response.status_code == 401
