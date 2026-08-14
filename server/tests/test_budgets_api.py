"""Budget cap CRUD, validation, and override (api/budgets.py, #97) through
the real HTTP API. Enforcement itself (dispatch/budgets.py wired into
dispatch/service.py) is covered separately in test_budget_enforcement.py --
this file only exercises the management surface.
"""

from fastapi.testclient import TestClient

from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope


def _create_agent(client: TestClient, headers: dict[str, str], name: str = "Budget Agent") -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "Test agent",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    return agent_id


def _create_team(client: TestClient, headers: dict[str, str], agent_ids: list[str]) -> str:
    team = client.post("/api/v1/teams", json={"name": "Budget Test Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id: str = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    return team_id


def test_create_workspace_scope_cap_and_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 10.0, "action": "alert"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scope_type"] == "workspace"
    assert body["agent_id"] is None
    assert body["team_id"] is None
    assert body["enabled"] is True

    listed = client.get("/api/v1/budgets", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    caps = listed.json()
    assert len(caps) == 1
    assert caps[0]["id"] == body["id"]
    assert caps[0]["spend_usd"] == 0
    assert caps[0]["breached"] is False
    assert caps[0]["blocked"] is False


def test_create_agent_scope_cap_requires_matching_agent_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # scope_type='agent' with no agent_id fails pydantic validation before
    # ever reaching the DB -- same "exactly one subject" precedent
    # EvalSuiteCreate already established for its own agent_id/workflow_id.
    response = client.post(
        "/api/v1/budgets",
        json={"scope_type": "agent", "period": "day", "limit_usd": 10.0, "action": "alert"},
        headers=auth_headers,
    )
    assert response.status_code == 422, response.text


def test_create_workspace_scope_cap_rejects_a_stray_agent_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    response = client.post(
        "/api/v1/budgets",
        json={
            "scope_type": "workspace",
            "agent_id": agent_id,
            "period": "day",
            "limit_usd": 10.0,
            "action": "alert",
        },
        headers=auth_headers,
    )
    assert response.status_code == 422, response.text


def test_create_agent_scope_cap_404s_on_unknown_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/budgets",
        json={
            "scope_type": "agent",
            "agent_id": "does-not-exist",
            "period": "day",
            "limit_usd": 10.0,
            "action": "hard_stop",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404, response.text


def test_create_cap_rejects_non_positive_limit(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 0, "action": "alert"},
        headers=auth_headers,
    )
    assert response.status_code == 422, response.text


def test_update_and_delete_cap_round_trip(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 10.0, "action": "alert"},
        headers=auth_headers,
    ).json()
    cap_id = created["id"]

    updated = client.patch(
        f"/api/v1/budgets/{cap_id}",
        json={"limit_usd": 25.0, "action": "hard_stop", "enabled": False},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["limit_usd"] == 25.0
    assert body["action"] == "hard_stop"
    assert body["enabled"] is False

    deleted = client.delete(f"/api/v1/budgets/{cap_id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text

    listed = client.get("/api/v1/budgets", headers=auth_headers).json()
    assert listed == []


async def test_delete_cap_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#287: the `client` fixture never actually starts the sync engine, so
    a successful delete queues a tombstone retry (SyncPendingOutbound.
    deleted=True) instead of the delete never reaching any peer at all --
    mirrors test_teams_api.py's equivalent for delete_team."""
    created = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 10.0, "action": "alert"},
        headers=auth_headers,
    ).json()
    cap_id = created["id"]

    deleted = client.delete(f"/api/v1/budgets/{cap_id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("budget_cap", cap_id))
        assert pending is not None
        assert pending.deleted is True


def test_team_scope_cap_via_team(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    team_id = _create_team(client, auth_headers, [agent_id])

    created = client.post(
        "/api/v1/budgets",
        json={
            "scope_type": "team",
            "team_id": team_id,
            "period": "week",
            "limit_usd": 50.0,
            "action": "hard_stop",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["team_id"] == team_id


def test_mutating_routes_require_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    invite_headers = {"Authorization": f"Bearer {accepted['token']}"}

    response = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 10.0, "action": "alert"},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # Reads stay open to any grant, matching usage.py/dispatch.py.
    listed = client.get("/api/v1/budgets", headers=invite_headers)
    assert listed.status_code == 200, listed.text


def test_override_endpoint_on_a_non_breached_cap_still_marks_override_active(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 10.0, "action": "hard_stop"},
        headers=auth_headers,
    ).json()

    overridden = client.post(f"/api/v1/budgets/{created['id']}/override", headers=auth_headers)
    assert overridden.status_code == 200, overridden.text
    body = overridden.json()
    assert body["override_active"] is True
    assert body["blocked"] is False
