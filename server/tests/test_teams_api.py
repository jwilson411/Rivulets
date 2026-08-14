"""api/teams.py CRUD -- these routes are only ever exercised indirectly (via
dispatch/streaming/sync tests that create a team as setup) elsewhere in the
suite, so list/get-404/patch-name/patch-description/delete never get a
dedicated, direct test of their own."""

from fastapi.testclient import TestClient

from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope


def test_list_teams_returns_created_teams(client: TestClient, auth_headers: dict[str, str]) -> None:
    # #16 seeds a "Starter Team" on first login -- "empty" here means no
    # manually-created team yet, not zero rows.
    assert [t["name"] for t in client.get("/api/v1/teams", headers=auth_headers).json()] == [
        "Starter Team"
    ]

    created = client.post("/api/v1/teams", json={"name": "Engineering"}, headers=auth_headers)
    assert created.status_code == 201, created.text

    listed = client.get("/api/v1/teams", headers=auth_headers)
    assert listed.status_code == 200
    assert {t["name"] for t in listed.json()} == {"Starter Team", "Engineering"}


def test_get_team_returns_404_for_unknown_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/teams/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_update_team_name_only_leaves_description_untouched(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/teams", json={"name": "Old Name", "description": "Original"}, headers=auth_headers
    )
    team_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/teams/{team_id}", json={"name": "New Name"}, headers=auth_headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "New Name"
    assert updated.json()["description"] == "Original"


def test_update_team_description_only_leaves_name_untouched(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/teams", json={"name": "Stable Name"}, headers=auth_headers)
    team_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/teams/{team_id}", json={"description": "New description"}, headers=auth_headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Stable Name"
    assert updated.json()["description"] == "New description"


def test_update_team_returns_404_for_unknown_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/v1/teams/does-not-exist", json={"name": "x"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_delete_team_removes_it(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/v1/teams", json={"name": "Doomed"}, headers=auth_headers)
    team_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/teams/{team_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/teams/{team_id}", headers=auth_headers).status_code == 404


def test_delete_team_unassigns_its_channels(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    team_id = client.post(
        "/api/v1/teams", json={"name": "Doomed With Channel"}, headers=auth_headers
    ).json()["id"]
    channel_id = client.post(
        "/api/v1/channels", json={"name": "doomed-channel"}, headers=auth_headers
    ).json()["id"]
    assigned = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=auth_headers
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["team_id"] == team_id

    deleted = client.delete(f"/api/v1/teams/{team_id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text

    fetched = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["team_id"] is None


async def test_delete_team_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#238: mirrors test_agents_api.py's equivalent -- the `client`
    fixture never actually starts the sync engine, so a successful delete
    queues a tombstone retry (SyncPendingOutbound.deleted=True) instead of
    the delete never reaching any peer at all."""
    team_id = client.post(
        "/api/v1/teams", json={"name": "Doomed Sync"}, headers=auth_headers
    ).json()["id"]

    deleted = client.delete(f"/api/v1/teams/{team_id}", headers=auth_headers)
    assert deleted.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("team", team_id))
        assert pending is not None
        assert pending.deleted is True


def test_delete_team_returns_404_for_unknown_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.delete("/api/v1/teams/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


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


def test_update_team_agent_ids_sets_membership_in_order(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_a = _create_agent(client, auth_headers, "Agent A")
    agent_b = _create_agent(client, auth_headers, "Agent B")
    team_id = client.post("/api/v1/teams", json={"name": "Roster"}, headers=auth_headers).json()[
        "id"
    ]

    updated = client.patch(
        f"/api/v1/teams/{team_id}",
        json={"agent_ids": [agent_b, agent_a]},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["agent_ids"] == [agent_b, agent_a]

    fetched = client.get(f"/api/v1/teams/{team_id}", headers=auth_headers)
    assert fetched.json()["agent_ids"] == [agent_b, agent_a]


def test_update_team_agent_ids_replaces_previous_membership(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_a = _create_agent(client, auth_headers, "Agent A2")
    agent_b = _create_agent(client, auth_headers, "Agent B2")
    team_id = client.post("/api/v1/teams", json={"name": "Roster2"}, headers=auth_headers).json()[
        "id"
    ]

    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_a]}, headers=auth_headers)
    replaced = client.patch(
        f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_b]}, headers=auth_headers
    )
    assert replaced.json()["agent_ids"] == [agent_b]


# #326: invite-grant escalation via team membership -- see api/teams.py's
# team_holds_owner_scoped_agent/update_team docstrings.


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def test_invite_grant_cannot_add_owner_scoped_agent_to_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    scoped_agent = _create_agent(client, auth_headers, "Scoped")
    client.put(
        f"/api/v1/agents/{scoped_agent}/tool-scopes",
        json={"scopes": ["invites:manage"]},
        headers=auth_headers,
    )
    team_id = client.post(
        "/api/v1/teams", json={"name": "Guest Controlled"}, headers=auth_headers
    ).json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/teams/{team_id}",
        json={"agent_ids": [scoped_agent]},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # An owner session can, same request otherwise.
    owner_response = client.patch(
        f"/api/v1/teams/{team_id}",
        json={"agent_ids": [scoped_agent]},
        headers=auth_headers,
    )
    assert owner_response.status_code == 200, owner_response.text


def test_invite_grant_can_add_unscoped_agent_to_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    unscoped_agent = _create_agent(client, auth_headers, "Unscoped Membership")
    team_id = client.post(
        "/api/v1/teams", json={"name": "Guest Controlled Fine"}, headers=auth_headers
    ).json()["id"]
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/teams/{team_id}",
        json={"agent_ids": [unscoped_agent]},
        headers=invite_headers,
    )
    assert response.status_code == 200, response.text


def test_invite_grant_can_keep_already_member_scoped_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Reordering/keeping an agent already on the team (added while it had
    no scope, or by an owner) isn't itself a new escalation -- only a
    *newly added* scoped agent is blocked."""
    scoped_agent = _create_agent(client, auth_headers, "Already Member")
    other_agent = _create_agent(client, auth_headers, "Also Member")
    team_id = client.post(
        "/api/v1/teams", json={"name": "Pre-populated"}, headers=auth_headers
    ).json()["id"]
    client.patch(
        f"/api/v1/teams/{team_id}",
        json={"agent_ids": [scoped_agent, other_agent]},
        headers=auth_headers,
    )
    client.put(
        f"/api/v1/agents/{scoped_agent}/tool-scopes",
        json={"scopes": ["invites:manage"]},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/teams/{team_id}",
        json={"agent_ids": [other_agent, scoped_agent]},
        headers=invite_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_ids"] == [other_agent, scoped_agent]
