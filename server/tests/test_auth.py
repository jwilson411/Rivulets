from fastapi.testclient import TestClient

from agent_hive.security import keys


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
