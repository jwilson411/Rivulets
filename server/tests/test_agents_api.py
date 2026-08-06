"""Agent CRUD and routing rules (api/agents.py, FR-3).

No prior test file exercised list/get/update/delete on this router
directly -- only agent creation + routing-rules endpoints, via
test_rule_generation.py, test_rivulet_dispatch.py, etc. Provider setup
is skipped throughout (pick_dispatcher_model returns None with no
provider configured -- see dispatch/rule_generation.py's module
docstring), so agent creation here never makes a real LLM call.
"""

from fastapi.testclient import TestClient


def _create_agent(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    name: str = "DBA",
    tool_ids: list[str] | None = None,
    team_ids: list[str] | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": tool_ids or [],
            "team_ids": team_ids or [],
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_list_agents_empty_then_populated(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/agents", headers=auth_headers).json() == []

    _create_agent(client, auth_headers)

    listed = client.get("/api/v1/agents", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "DBA"


def test_get_agent_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/agents/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_get_agent_success(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "DBA"


def test_create_agent_with_tools_and_teams(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = client.post(
        "/api/v1/tools",
        json={"name": "custom_tool", "description": "Does a thing."},
        headers=auth_headers,
    ).json()
    team = client.post("/api/v1/teams", json={"name": "Data Team"}, headers=auth_headers).json()

    agent = _create_agent(client, auth_headers, tool_ids=[tool["id"]], team_ids=[team["id"]])

    team_detail = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert team_detail["agent_ids"] == [agent["id"]]


def test_update_agent_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.patch(
        "/api/v1/agents/nonexistent", json={"name": "New name"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_update_agent_name_only_does_not_regenerate_rules(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    before_rules = client.get(
        f"/api/v1/agents/{agent['id']}/routing-rules", headers=auth_headers
    ).json()

    updated = client.patch(
        f"/api/v1/agents/{agent['id']}", json={"name": "Senior DBA"}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Senior DBA"

    after_rules = client.get(
        f"/api/v1/agents/{agent['id']}/routing-rules", headers=auth_headers
    ).json()
    assert after_rules == before_rules


def test_update_agent_description_triggers_rule_regeneration(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Changing description is one of rule_regen_fields -- existing rules
    (there are none here, with no provider configured) get cleared and
    regeneration is attempted again, still safely a no-op."""
    agent = _create_agent(client, auth_headers)

    updated = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"description": "Handles only PostgreSQL-specific questions now."},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Handles only PostgreSQL-specific questions now."


def test_update_agent_tool_ids_and_team_ids(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    tool = client.post(
        "/api/v1/tools",
        json={"name": "custom_tool", "description": "Does a thing."},
        headers=auth_headers,
    ).json()
    team = client.post("/api/v1/teams", json={"name": "Data Team"}, headers=auth_headers).json()
    agent = _create_agent(client, auth_headers)

    updated = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"tool_ids": [tool["id"]], "team_ids": [team["id"]]},
        headers=auth_headers,
    )
    assert updated.status_code == 200


def test_create_auto_mode_agent_registers_against_cheap_tier_default(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """model='auto' (#23) has no provider configured to resolve directly
    -- it must still register successfully, falling back to the default
    provider's cheap-tier model (agentos/service.py's _build_agno_agent)."""
    added_provider = client.post(
        "/api/v1/providers",
        json={"provider": "anthropic", "label": "Anthropic", "api_key": "sk-ant-test"},
        headers=auth_headers,
    )
    assert added_provider.status_code == 201, added_provider.text

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Auto Agent",
            "description": "Picks its own model per message.",
            "instructions": "Be helpful.",
            "model": "auto",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["model"] == "auto"
    assert agent["agentos_agent_id"] == agent["id"]  # registered, not skipped


def test_delete_agent_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.delete("/api/v1/agents/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_delete_agent_removes_it(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)

    deleted = client.delete(f"/api/v1/agents/{agent['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    assert client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers).status_code == 404
    assert client.get("/api/v1/agents", headers=auth_headers).json() == []


def test_get_agent_runs_not_implemented(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.get(f"/api/v1/agents/{agent['id']}/runs", headers=auth_headers)
    assert response.status_code == 501


def test_get_agent_runs_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/agents/nonexistent/runs", headers=auth_headers)
    assert response.status_code == 404
