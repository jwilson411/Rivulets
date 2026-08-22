"""Workspace export/import as YAML (NFR-8.1, #519) — GET /settings/export
and POST /settings/import, backed by workspace_yaml.py.

The round-trip test deliberately restores an earlier export over a
*mutated* workspace (rather than importing into a second fresh one):
export → drift → import-the-export → everything back, ids stable. That's
the config-as-code loop the issue's persona actually runs, and it
exercises both the id-match update path and the settings upsert.
"""

import yaml
from fastapi.testclient import TestClient

IMPORT_HEADERS = {"Content-Type": "application/yaml"}


def _export(client: TestClient, auth_headers: dict[str, str]) -> str:
    response = client.get("/api/v1/settings/export", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/yaml")
    return response.text


def _import(client: TestClient, auth_headers: dict[str, str], text: str) -> dict:
    response = client.post(
        "/api/v1/settings/import",
        content=text,
        headers={**auth_headers, **IMPORT_HEADERS},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _populate(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    """Team + custom tool (with real source) + agent (on the team, holding
    the tool) + channel (pointing at the team) + a non-default setting."""
    team = client.post(
        "/api/v1/teams",
        json={"name": "Research", "description": "Deep dives"},
        headers=auth_headers,
    ).json()
    tool = client.post(
        "/api/v1/tools",
        json={"name": "shout", "description": "Uppercases text"},
        headers=auth_headers,
    ).json()
    saved = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": "def shout(text: str) -> str:\n    return text.upper()\n"},
        headers=auth_headers,
    )
    assert saved.status_code == 201, saved.text
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Analyst",
            "description": "Digs into questions thoroughly",
            "instructions": "Research things.",
            "model": "openai:gpt-4o-mini",
            "tool_ids": [tool["id"]],
            "team_ids": [team["id"]],
        },
        headers=auth_headers,
    ).json()
    channel = client.post(
        "/api/v1/channels",
        json={"name": "research-hq", "team_id": team["id"]},
        headers=auth_headers,
    ).json()
    settings = client.patch("/api/v1/settings", json={"guard.turn_limit": 4}, headers=auth_headers)
    assert settings.status_code == 200, settings.text
    return {
        "team_id": team["id"],
        "tool_id": tool["id"],
        "agent_id": agent["id"],
        "channel_id": channel["id"],
    }


def test_export_walks_all_entities_and_omits_node_local_keys(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    ids = _populate(client, auth_headers)
    document = yaml.safe_load(_export(client, auth_headers))

    assert document["version"] == 1
    assert document["settings"]["guard.turn_limit"] == 4
    assert "ui.port" not in document["settings"]
    assert "tools.working_directory" not in document["settings"]

    # The seeded Starter Team is exported too — just check ours is there.
    assert ids["team_id"] in {t["id"] for t in document["teams"]}
    tool = next(t for t in document["tools"] if t["id"] == ids["tool_id"])
    assert "return text.upper()" in tool["source_code"]
    agent = next(a for a in document["agents"] if a["id"] == ids["agent_id"])
    assert {"type": "custom", "name": "shout"} in agent["tools"]
    assert agent["teams"] == [ids["team_id"]]
    channel = next(c for c in document["channels"] if c["id"] == ids["channel_id"])
    assert channel["team_id"] == ids["team_id"]


def test_import_restores_an_earlier_export_over_a_drifted_workspace(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    ids = _populate(client, auth_headers)
    snapshot = _export(client, auth_headers)

    # Drift: rename the agent, detach the channel from its team, drop the
    # agent's tools, and change a setting.
    client.patch(
        f"/api/v1/agents/{ids['agent_id']}",
        json={"name": "Renamed", "tool_ids": []},
        headers=auth_headers,
    )
    client.patch(
        f"/api/v1/channels/{ids['channel_id']}", json={"name": "off-topic"}, headers=auth_headers
    )
    client.patch("/api/v1/settings", json={"guard.turn_limit": 9}, headers=auth_headers)

    summary = _import(client, auth_headers, snapshot)
    assert summary["updated"].get("agent", 0) >= 1
    assert summary["created"] == {} or "agent" not in summary["created"]

    agents = {a["id"]: a for a in client.get("/api/v1/agents", headers=auth_headers).json()}
    assert agents[ids["agent_id"]]["name"] == "Analyst"
    tool_ids = client.get(f"/api/v1/agents/{ids['agent_id']}/tools", headers=auth_headers).json()[
        "tool_ids"
    ]
    assert ids["tool_id"] in tool_ids
    channel = client.get(f"/api/v1/channels/{ids['channel_id']}", headers=auth_headers).json()
    assert channel["name"] == "research-hq"
    assert channel["team_id"] == ids["team_id"]
    settings = client.get("/api/v1/settings", headers=auth_headers).json()
    assert settings["guard.turn_limit"] == 4


def test_import_is_idempotent(client: TestClient, auth_headers: dict[str, str]) -> None:
    _populate(client, auth_headers)
    snapshot = _export(client, auth_headers)
    _import(client, auth_headers, snapshot)
    summary = _import(client, auth_headers, snapshot)
    # Second import updates in place — nothing is duplicated.
    assert summary["created"] == {} or set(summary["created"]) <= {"setting"}
    agent_names = [a["name"] for a in client.get("/api/v1/agents", headers=auth_headers).json()]
    assert agent_names.count("Analyst") == 1
    channel_names = [c["name"] for c in client.get("/api/v1/channels", headers=auth_headers).json()]
    assert channel_names.count("research-hq") == 1


def test_import_creates_new_entities_with_assignments(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    document = {
        "version": 1,
        "settings": {},
        "teams": [{"name": "Ops", "description": "Operations"}],
        "tools": [
            {
                "name": "double",
                "description": "Doubles a number",
                "source_code": "def double(n: int) -> int:\n    return n * 2\n",
            }
        ],
        "agents": [
            {
                "name": "Operator",
                "description": "Keeps the lights on",
                "instructions": "Operate.",
                "model": "openai:gpt-4o-mini",
                "tools": [{"type": "custom", "name": "double"}],
                "teams": [],
            }
        ],
        "channels": [{"name": "ops-room"}],
    }
    summary = _import(client, auth_headers, yaml.safe_dump(document))
    assert summary["created"]["team"] == 1
    assert summary["created"]["tool"] == 1
    assert summary["created"]["agent"] == 1
    assert summary["created"]["channel"] == 1

    tools = client.get("/api/v1/tools", headers=auth_headers).json()
    created_tool = next(t for t in tools if t["name"] == "double")
    assert created_tool["tool_type"] == "custom"
    versions = client.get(
        f"/api/v1/tools/{created_tool['id']}/versions", headers=auth_headers
    ).json()
    assert "return n * 2" in versions[0]["source_code"]

    agent = next(
        a
        for a in client.get("/api/v1/agents", headers=auth_headers).json()
        if a["name"] == "Operator"
    )
    assigned = client.get(f"/api/v1/agents/{agent['id']}/tools", headers=auth_headers)
    assert created_tool["id"] in assigned.json()["tool_ids"]


def test_import_does_not_resurrect_a_deleted_team(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#238: the export predates the delete; importing it back must not
    bring the team back from the dead (the delete left a tombstone —
    here, the offline-delete marker publish_tombstone queues when the
    sync engine isn't running)."""
    team = client.post(
        "/api/v1/teams", json={"name": "Doomed", "description": "…"}, headers=auth_headers
    ).json()
    snapshot = _export(client, auth_headers)
    deleted = client.delete(f"/api/v1/teams/{team['id']}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text

    summary = _import(client, auth_headers, snapshot)
    assert any("Doomed" in entry for entry in summary["skipped"])
    team_names = [t["name"] for t in client.get("/api/v1/teams", headers=auth_headers).json()]
    assert "Doomed" not in team_names


def test_import_merges_onto_an_existing_agent_by_name(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """An export from another workspace carries different ids; a same-named
    agent (here the seeded Assistant) is updated in place, not duplicated
    and not a 409."""
    document = {
        "version": 1,
        "agents": [
            {
                "id": "some-foreign-workspace-id",
                "name": "Assistant",
                "description": "General helper, reconfigured via import",
                "instructions": "Help with everything.",
                "model": "openai:gpt-4o-mini",
            }
        ],
    }
    summary = _import(client, auth_headers, yaml.safe_dump(document))
    assert summary["updated"]["agent"] == 1
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistants = [a for a in agents if a["name"] == "Assistant"]
    assert len(assistants) == 1
    assert assistants[0]["description"] == "General helper, reconfigured via import"


def test_import_rejects_unknown_setting_key_without_writing_anything(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    document = {
        "version": 1,
        "settings": {"not.a.real.setting": 1, "guard.turn_limit": 2},
        "teams": [{"name": "ShouldNotExist"}],
    }
    response = client.post(
        "/api/v1/settings/import",
        content=yaml.safe_dump(document),
        headers={**auth_headers, **IMPORT_HEADERS},
    )
    assert response.status_code == 400
    assert any("not.a.real.setting" in err for err in response.json()["detail"])
    # Validation failed → nothing was applied, not even the valid parts.
    team_names = [t["name"] for t in client.get("/api/v1/teams", headers=auth_headers).json()]
    assert "ShouldNotExist" not in team_names
    settings = client.get("/api/v1/settings", headers=auth_headers).json()
    assert settings["guard.turn_limit"] == 10


def test_import_rejects_invalid_tool_source(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    document = {
        "version": 1,
        "tools": [{"name": "broken", "description": "x", "source_code": "def broken(:\n"}],
    }
    response = client.post(
        "/api/v1/settings/import",
        content=yaml.safe_dump(document),
        headers={**auth_headers, **IMPORT_HEADERS},
    )
    assert response.status_code == 400
    assert any("invalid Python" in err for err in response.json()["detail"])


def test_import_rejects_garbage_yaml(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/settings/import",
        content="just a string, not a mapping",
        headers={**auth_headers, **IMPORT_HEADERS},
    )
    assert response.status_code == 400


def test_import_skips_node_local_settings_with_a_warning(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    document = {"version": 1, "settings": {"ui.port": 9999, "guard.turn_limit": 3}}
    summary = _import(client, auth_headers, yaml.safe_dump(document))
    assert any("ui.port" in warning for warning in summary["warnings"])
    settings = client.get("/api/v1/settings", headers=auth_headers).json()
    assert settings["ui.port"] == 8484
    assert settings["guard.turn_limit"] == 3


def test_export_and_import_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/export").status_code == 401
    assert (
        client.post(
            "/api/v1/settings/import", content="version: 1", headers=IMPORT_HEADERS
        ).status_code
        == 401
    )
