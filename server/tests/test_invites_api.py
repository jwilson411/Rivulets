import asyncio

import pytest
from fastapi.testclient import TestClient

from rivulets.api.invites import _reserve_redemption_slot  # pyright: ignore[reportPrivateUsage]
from rivulets.db.session import session_scope
from rivulets.security.rate_limit import get_invite_accept_rate_limiter
from rivulets.security.session import get_session_key_store


def _extract_token(url: str) -> str:
    """create_invite's `url` is ".../invite/<id>.<secret>" -- accept_invite
    takes the "<id>.<secret>" pair as a single opaque `invite_token`."""
    return url.rsplit("/", 1)[-1]


def test_create_invite_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/invites", json={}, headers=auth_headers)
    assert response.status_code == 201, response.text
    assert response.json()["url"]
    assert response.json()["invite_id"]


def test_create_invite_over_a_non_loopback_host_is_not_flagged(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The `client` fixture's default base URL (http://testserver) isn't a
    loopback host, so this exercises the "already reachable" branch -- no
    warning, no LAN detection attempted."""
    response = client.post("/api/v1/invites", json={}, headers=auth_headers)
    body = response.json()
    assert body["loopback_only"] is False
    assert body["lan_url"] is None


def test_create_invite_over_loopback_offers_a_detected_lan_url(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.api.invites.detect_lan_address", lambda: "192.168.1.42")

    response = client.post(
        "/api/v1/invites",
        json={},
        headers={**auth_headers, "host": "127.0.0.1:8484"},
    )
    body = response.json()
    assert body["loopback_only"] is True
    assert body["lan_url"] is not None
    assert body["lan_url"].startswith("http://192.168.1.42:8484/invite/")
    assert body["lan_url"].rsplit("/", 1)[-1] == body["url"].rsplit("/", 1)[-1]


def test_create_invite_over_loopback_with_no_lan_address_detected_omits_lan_url(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.api.invites.detect_lan_address", lambda: None)

    response = client.post(
        "/api/v1/invites",
        json={},
        headers={**auth_headers, "host": "localhost:8484"},
    )
    body = response.json()
    assert body["loopback_only"] is True
    assert body["lan_url"] is None


def test_create_invite_is_forbidden_for_an_invite_grant_session(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": _extract_token(created["url"]), "display_name": "Guest One"},
    ).json()
    invite_headers = {"Authorization": f"Bearer {accepted['token']}"}

    response = client.post("/api/v1/invites", json={}, headers=invite_headers)
    assert response.status_code == 403


def test_accept_invite_succeeds_once_and_yields_a_working_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()

    response = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": _extract_token(created["url"]), "display_name": "Guest One"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Guest One"
    assert body["grant"] == "invite"
    assert body["human_id"]

    channel = client.get("/api/v1/channels", headers={"Authorization": f"Bearer {body['token']}"})
    assert channel.status_code == 200


def test_accept_invite_falls_back_to_display_name_hint_then_guest(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/invites", json={"display_name_hint": "Hinted Name"}, headers=auth_headers
    ).json()

    response = client.post(
        "/api/v1/invites/accept", json={"invite_token": _extract_token(created["url"])}
    )
    assert response.json()["display_name"] == "Hinted Name"


def test_second_accept_of_a_single_use_invite_fails(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    token = _extract_token(created["url"])

    first = client.post("/api/v1/invites/accept", json={"invite_token": token})
    assert first.status_code == 200

    second = client.post("/api/v1/invites/accept", json={"invite_token": token})
    assert second.status_code == 403


async def test_concurrent_redemption_claims_only_grant_one_slot(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#236: accept_invite used to check use_count < max_uses and increment
    it in separate steps, so two overlapping accepts could both pass the
    check before either committed. _reserve_redemption_slot's
    `UPDATE ... WHERE use_count < max_uses` folds the check and the write
    into one atomic statement instead, so only one of several truly
    concurrent claims against a max_uses=1 invite can ever win -- exercised
    here with real overlapping calls (via asyncio.gather, each on its own
    DB session), not just the sequential two-calls-in-a-row shape the
    existing tests already cover."""
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_id = created["invite_id"]

    async def _attempt() -> bool:
        async with session_scope() as session:
            claimed = await _reserve_redemption_slot(session, invite_id)
            await session.commit()
            return claimed

    results = await asyncio.gather(*[_attempt() for _ in range(8)])
    assert results.count(True) == 1, results
    assert results.count(False) == 7, results

    invites = client.get("/api/v1/invites", headers=auth_headers).json()
    assert invites[0]["use_count"] == 1


def test_accept_invite_with_max_uses_two_allows_exactly_two_redemptions(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/invites", json={"max_uses": 2}, headers=auth_headers).json()
    token = _extract_token(created["url"])

    assert client.post("/api/v1/invites/accept", json={"invite_token": token}).status_code == 200
    assert client.post("/api/v1/invites/accept", json={"invite_token": token}).status_code == 200
    assert client.post("/api/v1/invites/accept", json={"invite_token": token}).status_code == 403


def test_accept_an_expired_invite_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/invites", json={"expires_in_hours": -1}, headers=auth_headers
    ).json()

    response = client.post(
        "/api/v1/invites/accept", json={"invite_token": _extract_token(created["url"])}
    )
    assert response.status_code == 403
    assert "expired" in response.json()["detail"]


def test_accept_a_revoked_invite_is_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    revoke = client.delete(f"/api/v1/invites/{created['invite_id']}", headers=auth_headers)
    assert revoke.status_code == 204

    response = client.post(
        "/api/v1/invites/accept", json={"invite_token": _extract_token(created["url"])}
    )
    assert response.status_code == 403
    assert "revoked" in response.json()["detail"]


def test_accept_invite_with_a_wrong_secret_is_401(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_id = created["invite_id"]

    response = client.post(
        "/api/v1/invites/accept", json={"invite_token": f"{invite_id}.wrong-secret"}
    )
    assert response.status_code == 401


def test_accept_invite_with_an_unknown_id_is_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/invites/accept", json={"invite_token": "does-not-exist.some-secret"}
    )
    assert response.status_code == 401


def test_accept_invite_with_a_malformed_token_is_401(client: TestClient) -> None:
    response = client.post("/api/v1/invites/accept", json={"invite_token": "no-dot-here"})
    assert response.status_code == 401


def test_accept_invite_fails_cleanly_when_no_owner_session_is_active(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The signing key used to mint an invite-redeemed token only exists in
    the in-memory SessionKeyStore populated at login (#15's documented
    constraint: redemption depends on the owner's node already being
    unlocked) -- clearing it simulates the owner never having logged in
    on this node, or having since logged out."""
    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    get_session_key_store().clear()

    response = client.post(
        "/api/v1/invites/accept", json={"invite_token": _extract_token(created["url"])}
    )
    assert response.status_code == 401
    assert "unlocked" in response.json()["detail"]


def test_accept_invite_is_rate_limited_after_five_attempts_per_minute(
    client: TestClient,
) -> None:
    for _ in range(5):
        response = client.post("/api/v1/invites/accept", json={"invite_token": "bogus.bogus"})
        assert response.status_code == 401

    sixth = client.post("/api/v1/invites/accept", json={"invite_token": "bogus.bogus"})
    assert sixth.status_code == 429


def test_invite_accept_rate_limit_is_scoped_independently_per_ip() -> None:
    limiter = get_invite_accept_rate_limiter()
    for _ in range(5):
        assert limiter.check("1.2.3.4") is True
    assert limiter.check("1.2.3.4") is False
    assert limiter.check("5.6.7.8") is True


def test_list_invites_is_owner_only(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post("/api/v1/invites", json={}, headers=auth_headers)
    response = client.get("/api/v1/invites", headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_invites_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/invites").status_code == 401


def test_revoking_an_unknown_invite_is_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.delete("/api/v1/invites/does-not-exist", headers=auth_headers)
    assert response.status_code == 404
