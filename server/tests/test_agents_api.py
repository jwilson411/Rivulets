"""Agent CRUD and routing rules (api/agents.py, FR-3).

No prior test file exercised list/get/update/delete on this router
directly -- only agent creation + routing-rules endpoints, via
test_rule_generation.py, test_rivulet_dispatch.py, etc. Provider setup
is skipped throughout (pick_dispatcher_model returns None with no
provider configured -- see dispatch/rule_generation.py's module
docstring), so agent creation here never makes a real LLM call.
"""

from typing import cast

from fastapi.testclient import TestClient

from rivulets.db.models import MCPServer, SyncPendingOutbound, Tool
from rivulets.db.session import session_scope


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


def test_create_agent_duplicate_name_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create_agent(client, auth_headers, name="DBA")

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "DBA",
            "description": "A second agent claiming the same name.",
            "instructions": "You are also a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
        },
        headers=auth_headers,
    )
    assert response.status_code == 409, response.text


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


def test_update_agent_duplicate_name_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create_agent(client, auth_headers, name="DBA")
    other = _create_agent(client, auth_headers, name="Other")

    response = client.patch(
        f"/api/v1/agents/{other['id']}", json={"name": "DBA"}, headers=auth_headers
    )
    assert response.status_code == 409, response.text
    assert client.get(f"/api/v1/agents/{other['id']}", headers=auth_headers).json()["name"] == (
        "Other"
    )


def test_update_agent_name_unchanged_does_not_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers, name="DBA")

    response = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"name": "DBA", "description": "Updated description text here."},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text


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


async def test_delete_agent_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#238: without publish_tombstone, a peer that still has this agent's
    row would keep it forever, and its next edit would recreate it here.
    The `client` fixture never actually starts the sync engine (see
    conftest.py), so a successful publish attempt queues a tombstone
    (SyncPendingOutbound.deleted=True) instead of dropping the delete on
    the floor -- the same FR-9.5 offline-outbox behavior every other
    publish call site already gets."""
    agent = _create_agent(client, auth_headers)

    deleted = client.delete(f"/api/v1/agents/{agent['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("agent", agent["id"]))
        assert pending is not None
        assert pending.deleted is True


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


def test_get_agent_tool_scopes_empty_by_default(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.get(f"/api/v1/agents/{agent['id']}/tool-scopes", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"scopes": []}


def test_get_agent_tool_scopes_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/agents/nonexistent/tool-scopes", headers=auth_headers)
    assert response.status_code == 404


def test_set_agent_tool_scopes_grants_and_replaces(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)

    granted = client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["channels:manage", "workflows:manage"]},
        headers=auth_headers,
    )
    assert granted.status_code == 200, granted.text
    assert granted.json() == {"scopes": ["channels:manage", "workflows:manage"]}

    fetched = client.get(f"/api/v1/agents/{agent['id']}/tool-scopes", headers=auth_headers)
    assert fetched.json() == {"scopes": ["channels:manage", "workflows:manage"]}

    # A second PUT fully replaces the set rather than merging into it.
    replaced = client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["settings:manage"]},
        headers=auth_headers,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json() == {"scopes": ["settings:manage"]}


def test_set_agent_tool_scopes_rejects_unknown_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    response = client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["not_a_real_scope"]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "not_a_real_scope" in response.text


def test_set_agent_tool_scopes_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/v1/agents/nonexistent/tool-scopes",
        json={"scopes": []},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_set_agent_tool_scopes_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)

    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    invite_headers = {"Authorization": f"Bearer {accepted['token']}"}

    response = client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # Reading isn't owner-gated, same as the rest of this router.
    read = client.get(f"/api/v1/agents/{agent['id']}/tool-scopes", headers=invite_headers)
    assert read.status_code == 200


# #231: invite-grant escalation paths -- see api/agents.py's
# _check_tool_assignment_authorized/agent_holds_owner_scope docstrings.


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def _tool_id_by_name(client: TestClient, auth_headers: dict[str, str], name: str) -> str:
    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    (tool,) = [t for t in tools if t["name"] == name]
    return cast(str, tool["id"])


def test_create_agent_with_sensitive_builtin_tool_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invite_headers = _invite_headers(client, auth_headers)
    execute_python_id = _tool_id_by_name(client, auth_headers, "execute_python")

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Sneaky",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [execute_python_id],
        },
        headers=invite_headers,
    )
    assert response.status_code == 403

    # An owner session can, same request otherwise.
    owner_created = client.post(
        "/api/v1/agents",
        json={
            "name": "Sneaky",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [execute_python_id],
        },
        headers=auth_headers,
    )
    assert owner_created.status_code == 201, owner_created.text


def test_create_agent_with_scoped_non_sensitive_tool_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """create_invite isn't in SENSITIVE_BUILTIN_TOOL_NAMES, but it does
    carry a required_scope (invites:manage) -- the "any tool with a
    required_scope" arm of the gate, not just the fixed sensitive set."""
    invite_headers = _invite_headers(client, auth_headers)
    create_invite_id = _tool_id_by_name(client, auth_headers, "create_invite")

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Sneaky",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [create_invite_id],
        },
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_create_agent_with_custom_tool_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#285: a custom tool has required_scope=None (that field only
    exists for builtins), but its code still runs unsandboxed in the App
    Server process -- it must be gated the same as a sensitive builtin,
    not fall through as "unscoped"."""
    invite_headers = _invite_headers(client, auth_headers)
    custom_tool = client.post(
        "/api/v1/tools",
        json={"name": "custom_tool", "description": "Does a thing."},
        headers=auth_headers,
    ).json()

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Harmless",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [custom_tool["id"]],
        },
        headers=invite_headers,
    )
    assert response.status_code == 403

    owner_created = client.post(
        "/api/v1/agents",
        json={
            "name": "Harmless",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [custom_tool["id"]],
        },
        headers=auth_headers,
    )
    assert owner_created.status_code == 201, owner_created.text


async def test_create_agent_with_mcp_tool_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#285: an MCP tool also has required_scope=None, and resolves at run
    time with the owner's stored headers/env (tool_resolution.py's
    get_server_headers/get_server_env) -- same gate as a custom tool."""
    invite_headers = _invite_headers(client, auth_headers)
    async with session_scope() as db:
        server = MCPServer(name="test-server", url="http://localhost:9999/mcp")
        db.add(server)
        await db.commit()
        tool_row = Tool(
            name="remote_tool",
            description="An MCP tool.",
            tool_type="mcp",
            mcp_server_id=server.id,
            mcp_tool_name="remote_tool",
        )
        db.add(tool_row)
        await db.commit()
        mcp_tool_id = tool_row.id

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Harmless",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [mcp_tool_id],
        },
        headers=invite_headers,
    )
    assert response.status_code == 403

    owner_created = client.post(
        "/api/v1/agents",
        json={
            "name": "Harmless",
            "description": "Handles database schema and SQL questions",
            "instructions": "You are a DBA.",
            "model": "anthropic:claude-haiku-4-5-20251001",
            "tool_ids": [mcp_tool_id],
        },
        headers=auth_headers,
    )
    assert owner_created.status_code == 201, owner_created.text


def test_update_agent_tool_ids_with_sensitive_tool_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    invite_headers = _invite_headers(client, auth_headers)
    execute_python_id = _tool_id_by_name(client, auth_headers, "execute_python")

    response = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"tool_ids": [execute_python_id]},
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_update_agent_approved_for_unattended_tools_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"approved_for_unattended_tools": True},
        headers=invite_headers,
    )
    assert response.status_code == 403

    owner_response = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"approved_for_unattended_tools": True},
        headers=auth_headers,
    )
    assert owner_response.status_code == 200, owner_response.text
    assert owner_response.json()["approved_for_unattended_tools"] is True


def test_update_agent_holding_owner_scope_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["invites:manage"]},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    # Even an unrelated field (name) is blocked -- once an agent holds a
    # standing scope, only the owner can touch it at all.
    response = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"name": "Renamed"},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # An agent with no granted scope stays open to ordinary edits.
    other_agent = _create_agent(client, auth_headers, name="Unscoped")
    ordinary = client.patch(
        f"/api/v1/agents/{other_agent['id']}",
        json={"name": "Renamed Fine"},
        headers=invite_headers,
    )
    assert ordinary.status_code == 200, ordinary.text


def test_update_routing_rules_on_agent_holding_owner_scope_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["settings:manage"]},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.patch(
        f"/api/v1/agents/{agent['id']}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": "db", "priority": 1}]},
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_rollback_agent_version_on_agent_holding_owner_scope_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"instructions": "You are an updated DBA."},
        headers=auth_headers,
    )
    client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["workflows:manage"]},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.post(
        f"/api/v1/agents/{agent['id']}/versions/1/rollback",
        headers=invite_headers,
    )
    assert response.status_code == 403


def test_delete_agent_holding_owner_scope_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["invites:manage"]},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.delete(f"/api/v1/agents/{agent['id']}", headers=invite_headers)
    assert response.status_code == 403

    # An agent with no granted scope stays open to ordinary deletion.
    other_agent = _create_agent(client, auth_headers, name="Unscoped")
    ordinary = client.delete(f"/api/v1/agents/{other_agent['id']}", headers=invite_headers)
    assert ordinary.status_code == 204


def test_set_peer_preference_on_agent_holding_owner_scope_requires_owner_grant(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent = _create_agent(client, auth_headers)
    client.put(
        f"/api/v1/agents/{agent['id']}/tool-scopes",
        json={"scopes": ["settings:manage"]},
        headers=auth_headers,
    )
    invite_headers = _invite_headers(client, auth_headers)

    response = client.put(
        f"/api/v1/agents/{agent['id']}/peer-preference",
        json={"capability_tag": "gpu"},
        headers=invite_headers,
    )
    assert response.status_code == 403

    # An agent with no granted scope stays open to ordinary edits.
    other_agent = _create_agent(client, auth_headers, name="Unscoped")
    ordinary = client.put(
        f"/api/v1/agents/{other_agent['id']}/peer-preference",
        json={"capability_tag": "gpu"},
        headers=invite_headers,
    )
    assert ordinary.status_code == 200, ordinary.text
