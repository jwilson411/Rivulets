from fastapi.testclient import TestClient

from rivulets.security import keys
from rivulets.security.rate_limit import get_login_rate_limiter


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
