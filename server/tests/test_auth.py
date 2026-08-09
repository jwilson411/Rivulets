import pytest
from fastapi.testclient import TestClient

from rivulets.security import keys
from rivulets.security.rate_limit import get_login_rate_limiter
from rivulets.security.session import get_session_key_store


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


def test_login_derives_a_credential_store_key(client: TestClient) -> None:
    """#118: login always derives the encrypted-SQLite fallback's
    encryption key alongside the JWT signing key and P2P PSK, even though
    most nodes never end up reading it (session.py's module docstring) —
    it's only used when the OS keychain has no usable backend."""
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    assert response.status_code == 200

    assert get_session_key_store().get_credential_store_key()
