from fastapi.testclient import TestClient


def test_channel_crud_lifecycle(client: TestClient, auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/v1/channels",
        json={"name": "general", "description": "General discussion"},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    channel = create.json()
    assert channel["name"] == "general"
    assert channel["archived"] is False

    listed = client.get("/api/v1/channels", headers=auth_headers)
    assert len(listed.json()) == 1

    renamed = client.patch(
        f"/api/v1/channels/{channel['id']}",
        json={"name": "general-discussion"},
        headers=auth_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "general-discussion"

    archived = client.delete(f"/api/v1/channels/{channel['id']}", headers=auth_headers)
    assert archived.status_code == 204

    active_only = client.get("/api/v1/channels", headers=auth_headers)
    assert all(c["archived"] for c in active_only.json() if c["id"] == channel["id"])

    restored = client.post(f"/api/v1/channels/{channel['id']}/unarchive", headers=auth_headers)
    assert restored.status_code == 200
    assert restored.json()["archived"] is False


def test_channel_name_length_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/channels", json={"name": "ab"}, headers=auth_headers)
    assert response.status_code == 400


def test_create_channel_accepts_team_id(client: TestClient, auth_headers: dict[str, str]) -> None:
    """#411: assigning a team at create so the first message can get a reply."""
    teams = client.get("/api/v1/teams", headers=auth_headers).json()
    starter = next(team for team in teams if "starter" in team["name"].lower())

    created = client.post(
        "/api/v1/channels",
        json={"name": "My Channel", "team_id": starter["id"]},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "My Channel"
    assert created.json()["team_id"] == starter["id"]


def test_create_channel_unknown_team_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/channels",
        json={"name": "orphan-room", "team_id": "team-does-not-exist"},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_duplicate_active_channel_name_returns_409_via_global_handler(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """create_channel has no pre-check of its own (unlike agents/workflows)
    -- this exercises app.py's #250 IntegrityError safety net against
    Channel's real partial unique index (idx_channel_name, archived = 0)
    instead of a contrived exception."""
    first = client.post("/api/v1/channels", json={"name": "duplicate-name"}, headers=auth_headers)
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/channels", json={"name": "duplicate-name"}, headers=auth_headers)
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["error"]["code"] == "conflict"
    assert "sqlite" not in second.text.lower()
    assert "INSERT INTO" not in second.text


def test_reorder_channels(client: TestClient, auth_headers: dict[str, str]) -> None:
    ids = []
    for name in ("alpha-chan", "beta-chan", "gamma-chan"):
        created = client.post("/api/v1/channels", json={"name": name}, headers=auth_headers)
        ids.append(created.json()["id"])

    reordered = client.patch(
        "/api/v1/channels/reorder",
        json={"order": list(reversed(ids))},
        headers=auth_headers,
    )
    assert reordered.status_code == 204

    listed = client.get("/api/v1/channels", headers=auth_headers).json()
    assert [c["id"] for c in listed] == list(reversed(ids))


# #326: invite-grant escalation via Channel.team_id -- see api/channels.py's
# update_channel docstring.


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def _create_agent(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "A test agent.",
            "instructions": "Be helpful.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_invite_grant_cannot_point_channel_at_team_with_scoped_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scoped_agent = _create_agent(client, auth_headers, "Channel Scoped")
    client.put(
        f"/api/v1/agents/{scoped_agent}/tool-scopes",
        json={"scopes": ["invites:manage"]},
        headers=auth_headers,
    )
    team_id = client.post(
        "/api/v1/teams", json={"name": "Scoped Team"}, headers=auth_headers
    ).json()["id"]
    client.patch(
        f"/api/v1/teams/{team_id}", json={"agent_ids": [scoped_agent]}, headers=auth_headers
    )
    channel_id = client.post(
        "/api/v1/channels", json={"name": "guest-retarget"}, headers=auth_headers
    ).json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/channels/{channel_id}",
        json={"team_id": team_id},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # An owner session can, same request otherwise.
    owner_response = client.patch(
        f"/api/v1/channels/{channel_id}",
        json={"team_id": team_id},
        headers=auth_headers,
    )
    assert owner_response.status_code == 200, owner_response.text


def test_invite_grant_cannot_create_channel_on_team_with_scoped_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scoped_agent = _create_agent(client, auth_headers, "Create Scoped")
    client.put(
        f"/api/v1/agents/{scoped_agent}/tool-scopes",
        json={"scopes": ["invites:manage"]},
        headers=auth_headers,
    )
    team_id = client.post(
        "/api/v1/teams", json={"name": "Create Scoped Team"}, headers=auth_headers
    ).json()["id"]
    client.patch(
        f"/api/v1/teams/{team_id}", json={"agent_ids": [scoped_agent]}, headers=auth_headers
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(
        "/api/v1/channels",
        json={"name": "guest-create-scoped", "team_id": team_id},
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_invite_grant_can_point_channel_at_team_without_scoped_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    team_id = client.post(
        "/api/v1/teams", json={"name": "Plain Team"}, headers=auth_headers
    ).json()["id"]
    channel_id = client.post(
        "/api/v1/channels", json={"name": "guest-retarget-fine"}, headers=auth_headers
    ).json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/channels/{channel_id}",
        json={"team_id": team_id},
        headers=invite_headers,
    )
    assert response.status_code == 200, response.text
