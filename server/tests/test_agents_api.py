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
    # #16 seeds four starter agents on first login -- "empty" here means
    # no manually-created agent yet, not zero rows.
    starter_names = {a["name"] for a in client.get("/api/v1/agents", headers=auth_headers).json()}
    assert starter_names == {"Assistant", "Coder", "Researcher", "Writer"}

    _create_agent(client, auth_headers)

    listed = client.get("/api/v1/agents", headers=auth_headers).json()
    assert len(listed) == 5
    assert "DBA" in {a["name"] for a in listed}


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


def test_create_agent_defaults_to_no_fallback_models(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    assert agent["fallback_models"] == []


def test_create_agent_with_fallback_models(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Resilient",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "fallback_models": ["openai:gpt-4o-mini", "deepseek:deepseek-chat"],
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["fallback_models"] == ["openai:gpt-4o-mini", "deepseek:deepseek-chat"]

    fetched = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers).json()
    assert fetched["fallback_models"] == ["openai:gpt-4o-mini", "deepseek:deepseek-chat"]


def test_update_agent_fallback_models(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    assert agent["fallback_models"] == []

    updated = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"fallback_models": ["openai:gpt-4o-mini"]},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["fallback_models"] == ["openai:gpt-4o-mini"]

    cleared = client.patch(
        f"/api/v1/agents/{agent['id']}", json={"fallback_models": []}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["fallback_models"] == []


def test_create_agent_defaults_to_no_output_schema(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    assert agent["output_schema"] is None


def test_create_agent_with_output_schema(client: TestClient, auth_headers: dict[str, str]) -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Extractor",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "output_schema": schema,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["output_schema"] == schema

    fetched = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers).json()
    assert fetched["output_schema"] == schema


def test_update_agent_output_schema(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    assert agent["output_schema"] is None

    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    updated = client.patch(
        f"/api/v1/agents/{agent['id']}", json={"output_schema": schema}, headers=auth_headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["output_schema"] == schema

    cleared = client.patch(
        f"/api/v1/agents/{agent['id']}", json={"output_schema": {}}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["output_schema"] is None


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
    # The four starter agents (#16) are untouched -- only the one created above is gone.
    remaining = {a["name"] for a in client.get("/api/v1/agents", headers=auth_headers).json()}
    assert remaining == {"Assistant", "Coder", "Researcher", "Writer"}


def test_get_agent_runs_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.get(f"/api/v1/agents/{agent['id']}/runs", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_get_agent_runs_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/agents/nonexistent/runs", headers=auth_headers)
    assert response.status_code == 404


def test_get_peer_preference_defaults_to_none(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.get(f"/api/v1/agents/{agent['id']}/peer-preference", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"capability_tag": None}


def test_get_peer_preference_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/agents/nonexistent/peer-preference", headers=auth_headers)
    assert response.status_code == 404


def test_set_and_clear_peer_preference(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)

    set_response = client.put(
        f"/api/v1/agents/{agent['id']}/peer-preference",
        json={"capability_tag": "gpu"},
        headers=auth_headers,
    )
    assert set_response.status_code == 200, set_response.text
    assert set_response.json() == {"capability_tag": "gpu"}

    read_back = client.get(f"/api/v1/agents/{agent['id']}/peer-preference", headers=auth_headers)
    assert read_back.json() == {"capability_tag": "gpu"}

    updated = client.put(
        f"/api/v1/agents/{agent['id']}/peer-preference",
        json={"capability_tag": "cpu-heavy"},
        headers=auth_headers,
    )
    assert updated.json() == {"capability_tag": "cpu-heavy"}

    cleared = client.put(
        f"/api/v1/agents/{agent['id']}/peer-preference",
        json={"capability_tag": None},
        headers=auth_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"capability_tag": None}

    read_after_clear = client.get(
        f"/api/v1/agents/{agent['id']}/peer-preference", headers=auth_headers
    )
    assert read_after_clear.json() == {"capability_tag": None}


def test_set_peer_preference_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/v1/agents/nonexistent/peer-preference",
        json={"capability_tag": "gpu"},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_create_agent_records_version_one(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    versions = client.get(f"/api/v1/agents/{agent['id']}/versions", headers=auth_headers).json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["instructions"] == "You are a DBA."
    assert versions[0]["model"] == "anthropic:claude-haiku-4-5-20251001"


def test_list_agent_versions_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/agents/nonexistent/versions", headers=auth_headers)
    assert response.status_code == 404


def test_update_agent_instructions_adds_a_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)

    updated = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"instructions": "You are a senior DBA now."},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text

    versions = client.get(f"/api/v1/agents/{agent['id']}/versions", headers=auth_headers).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["instructions"] == "You are a senior DBA now."
    assert versions[1]["instructions"] == "You are a DBA."


def test_update_agent_name_only_does_not_add_a_version(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)

    updated = client.patch(
        f"/api/v1/agents/{agent['id']}", json={"name": "Senior DBA"}, headers=auth_headers
    )
    assert updated.status_code == 200

    versions = client.get(f"/api/v1/agents/{agent['id']}/versions", headers=auth_headers).json()
    assert len(versions) == 1


def test_rollback_agent_version(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"instructions": "You are a senior DBA now.", "model": "openai:gpt-4o-mini"},
        headers=auth_headers,
    )

    rolled_back = client.post(
        f"/api/v1/agents/{agent['id']}/versions/1/rollback", headers=auth_headers
    )
    assert rolled_back.status_code == 200, rolled_back.text
    body = rolled_back.json()
    assert body["instructions"] == "You are a DBA."
    assert body["model"] == "anthropic:claude-haiku-4-5-20251001"

    fetched = client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers).json()
    assert fetched["instructions"] == "You are a DBA."
    assert fetched["model"] == "anthropic:claude-haiku-4-5-20251001"

    # The rollback itself is recorded as a new (third) version.
    versions = client.get(f"/api/v1/agents/{agent['id']}/versions", headers=auth_headers).json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert versions[0]["instructions"] == "You are a DBA."


def test_rollback_agent_version_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.post(
        f"/api/v1/agents/{agent['id']}/versions/99/rollback", headers=auth_headers
    )
    assert response.status_code == 404


def test_rollback_agent_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/agents/nonexistent/versions/1/rollback", headers=auth_headers)
    assert response.status_code == 404
