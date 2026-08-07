from fastapi.testclient import TestClient

from rivulets.security import keys


def test_list_humans_is_empty_on_a_fresh_workspace(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    headers = {"Authorization": f"Bearer {response.json()['token']}"}

    assert client.get("/api/v1/humans", headers=headers).json() == []


def test_list_humans_includes_claimed_identities(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """auth_headers already claims "Test User" (see conftest.py)."""
    response = client.get("/api/v1/humans", headers=auth_headers)
    assert response.status_code == 200
    assert [h["display_name"] for h in response.json()] == ["Test User"]


def test_list_humans_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/humans").status_code == 401
