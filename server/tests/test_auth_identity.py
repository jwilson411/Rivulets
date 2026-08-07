"""POST /auth/identity (#14) -- claiming a Human identity on top of the
existing workspace-level auth. Named test_auth_identity.py rather than
test_identity.py, which is already taken by sync/identity.py's node-key
generation tests."""

from fastapi.testclient import TestClient

from rivulets.security import keys


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_claiming_a_new_display_name_creates_a_human(client: TestClient) -> None:
    headers = _login(client)
    response = client.post(
        "/api/v1/auth/identity", json={"display_name": "Ada"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Ada"
    assert body["grant"] == "owner"
    assert body["human_id"]
    assert body["token"]

    humans = client.get(
        "/api/v1/humans", headers={"Authorization": f"Bearer {body['token']}"}
    ).json()
    assert [h["display_name"] for h in humans] == ["Ada"]


def test_reclaiming_an_existing_human_id_does_not_duplicate_it(client: TestClient) -> None:
    headers = _login(client)
    first = client.post(
        "/api/v1/auth/identity", json={"display_name": "Ada"}, headers=headers
    ).json()

    second = client.post(
        "/api/v1/auth/identity", json={"human_id": first["human_id"]}, headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["human_id"] == first["human_id"]
    assert second.json()["display_name"] == "Ada"

    humans = client.get(
        "/api/v1/humans", headers={"Authorization": f"Bearer {second.json()['token']}"}
    ).json()
    assert len(humans) == 1


def test_claiming_an_unknown_human_id_is_404(client: TestClient) -> None:
    headers = _login(client)
    response = client.post(
        "/api/v1/auth/identity", json={"human_id": "does-not-exist"}, headers=headers
    )
    assert response.status_code == 404


def test_providing_neither_human_id_nor_display_name_is_400(client: TestClient) -> None:
    headers = _login(client)
    response = client.post("/api/v1/auth/identity", json={}, headers=headers)
    assert response.status_code == 400


def test_providing_both_human_id_and_display_name_is_400(client: TestClient) -> None:
    headers = _login(client)
    response = client.post(
        "/api/v1/auth/identity",
        json={"human_id": "x", "display_name": "y"},
        headers=headers,
    )
    assert response.status_code == 400


def test_posting_a_message_before_claiming_an_identity_is_401(client: TestClient) -> None:
    headers = _login(client)
    channel = client.post("/api/v1/channels", json={"name": "general"}, headers=headers)
    assert channel.status_code == 201, channel.text

    response = client.post(
        f"/api/v1/channels/{channel.json()['id']}/rivulets",
        json={"content": "hello"},
        headers=headers,
    )
    assert response.status_code == 401


def test_posting_a_message_after_claiming_sets_real_sender_fields(client: TestClient) -> None:
    headers = _login(client)
    identity = client.post(
        "/api/v1/auth/identity", json={"display_name": "Ada"}, headers=headers
    ).json()
    claimed_headers = {"Authorization": f"Bearer {identity['token']}"}

    channel = client.post(
        "/api/v1/channels", json={"name": "general"}, headers=claimed_headers
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel.json()['id']}/rivulets",
        json={"content": "hello"},
        headers=claimed_headers,
    )
    assert rivulet.status_code == 201, rivulet.text

    messages = client.get(
        f"/api/v1/rivulets/{rivulet.json()['id']}/messages", headers=claimed_headers
    ).json()
    human_message = next(m for m in messages if m["sender_type"] == "human")
    assert human_message["sender_name"] == "Ada"
    assert human_message["sender_id"] == identity["human_id"]
