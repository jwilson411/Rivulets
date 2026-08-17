"""#190: agent-facing agent & team management -- the create_agent/
update_agent/delete_agent/update_agent_routing_rules/
update_agent_peer_preference/rollback_agent_version/create_team/
update_team/delete_team/list_agents/list_teams tools
(tools/builtin/agents_teams.py) and their detection + handling in
dispatch/service.py. Mirrors test_channel_tools.py's style: agentos.
run_agent is monkeypatched to hand back a RunOutput with the tool call
already baked in, since real tool-call detection by a live model isn't
something a fake API key can produce.
"""

from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.db.models import MCPServer, SyncPendingOutbound, Tool
from rivulets.db.session import session_scope
from rivulets.dispatch.service import (
    _find_create_agent_call,  # pyright: ignore[reportPrivateUsage]
    _find_create_team_call,  # pyright: ignore[reportPrivateUsage]
    _find_delete_agent_call,  # pyright: ignore[reportPrivateUsage]
    _find_delete_team_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_agents_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_teams_call,  # pyright: ignore[reportPrivateUsage]
    _find_rollback_agent_version_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_agent_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_agent_peer_preference_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_agent_routing_rules_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_team_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.agents_teams import (
    create_agent,
    create_team,
    delete_agent,
    delete_team,
    list_agents,
    list_teams,
    rollback_agent_version,
    update_agent,
    update_agent_peer_preference,
    update_agent_routing_rules,
    update_team,
)
from tests.conftest import authorize_agent_for_builtin_tool  # pyright: ignore[reportMissingImports]


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


# --- tool entrypoints -------------------------------------------------


def test_create_agent_tool_returns_confirmation_string() -> None:
    assert create_agent.entrypoint is not None
    result = create_agent.entrypoint(
        name="DBA",
        description="Handles DB questions",
        instructions="Answer SQL questions.",
        model="anthropic:claude-3-5-haiku-latest",
    )
    assert "DBA" in result


def test_update_agent_tool_returns_confirmation_string() -> None:
    assert update_agent.entrypoint is not None
    assert "DBA" in update_agent.entrypoint(agent="DBA", name="DBA-2")


def test_delete_agent_tool_returns_confirmation_string() -> None:
    assert delete_agent.entrypoint is not None
    assert "DBA" in delete_agent.entrypoint(agent="DBA")


def test_update_agent_routing_rules_tool_returns_confirmation_string() -> None:
    assert update_agent_routing_rules.entrypoint is not None
    result = update_agent_routing_rules.entrypoint(
        agent="DBA", rules=[{"rule_type": "always", "pattern": "", "priority": 0}]
    )
    assert "DBA" in result
    assert "1" in result


def test_update_agent_peer_preference_tool_returns_confirmation_string() -> None:
    assert update_agent_peer_preference.entrypoint is not None
    assert "gpu" in update_agent_peer_preference.entrypoint(agent="DBA", capability_tag="gpu")
    assert "DBA" in update_agent_peer_preference.entrypoint(agent="DBA")


def test_rollback_agent_version_tool_returns_confirmation_string() -> None:
    assert rollback_agent_version.entrypoint is not None
    result = rollback_agent_version.entrypoint(agent="DBA", version=2)
    assert "DBA" in result
    assert "2" in result


def test_create_team_tool_returns_confirmation_string() -> None:
    assert create_team.entrypoint is not None
    assert "Platform" in create_team.entrypoint(name="Platform")


def test_update_team_tool_returns_confirmation_string() -> None:
    assert update_team.entrypoint is not None
    assert "Platform" in update_team.entrypoint(team="Platform", name="Platform-2")


def test_delete_team_tool_returns_confirmation_string() -> None:
    assert delete_team.entrypoint is not None
    assert "Platform" in delete_team.entrypoint(team="Platform")


def test_list_agents_tool_returns_confirmation_string() -> None:
    assert list_agents.entrypoint is not None
    assert "agent" in list_agents.entrypoint().lower()


def test_list_teams_tool_returns_confirmation_string() -> None:
    assert list_teams.entrypoint is not None
    assert "team" in list_teams.entrypoint().lower()


# --- tool-call parsers --------------------------------------------------


def test_find_create_agent_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "create_agent",
                {
                    "name": "DBA",
                    "description": "Handles DB questions",
                    "instructions": "Answer SQL questions.",
                    "model": "anthropic:claude-3-5-haiku-latest",
                    "team_ids": ["team-1"],
                },
            )
        ],
    )
    call = _find_create_agent_call(run_output)
    assert call is not None
    assert call.name == "DBA"
    assert call.description == "Handles DB questions"
    assert call.team_ids == ["team-1"]


def test_find_create_agent_call_missing_name_returns_none() -> None:
    run_output = RunOutput(status=RunStatus.completed, tools=[_tool_execution("create_agent", {})])
    assert _find_create_agent_call(run_output) is None


def test_find_update_agent_call_preserves_none_vs_empty_list() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("update_agent", {"agent": "DBA", "name": "DBA-2", "team_ids": []})],
    )
    call = _find_update_agent_call(run_output)
    assert call is not None
    assert call.agent_ref == "DBA"
    assert call.name == "DBA-2"
    assert call.team_ids == []
    assert call.tool_ids is None


def test_find_delete_agent_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("delete_agent", {"agent": "DBA"})]
    )
    assert _find_delete_agent_call(run_output) == "DBA"


def test_find_update_agent_routing_rules_call_extracts_rules() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "update_agent_routing_rules",
                {"agent": "DBA", "rules": [{"rule_type": "always", "priority": 5}]},
            )
        ],
    )
    call = _find_update_agent_routing_rules_call(run_output)
    assert call is not None
    assert call.agent_ref == "DBA"
    assert call.rules == [{"rule_type": "always", "priority": 5}]


def test_find_update_agent_routing_rules_call_non_dict_items_returns_none() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("update_agent_routing_rules", {"agent": "DBA", "rules": ["bad"]})],
    )
    assert _find_update_agent_routing_rules_call(run_output) is None


def test_find_update_agent_peer_preference_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "update_agent_peer_preference", {"agent": "DBA", "capability_tag": "gpu"}
            )
        ],
    )
    call = _find_update_agent_peer_preference_call(run_output)
    assert call is not None
    assert call.agent_ref == "DBA"
    assert call.capability_tag == "gpu"


def test_find_rollback_agent_version_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("rollback_agent_version", {"agent": "DBA", "version": 1})],
    )
    call = _find_rollback_agent_version_call(run_output)
    assert call is not None
    assert call.agent_ref == "DBA"
    assert call.version == 1


def test_find_rollback_agent_version_call_rejects_bool_version() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("rollback_agent_version", {"agent": "DBA", "version": True})],
    )
    assert _find_rollback_agent_version_call(run_output) is None


def test_find_list_agents_call() -> None:
    run_output = RunOutput(status=RunStatus.completed, tools=[_tool_execution("list_agents", {})])
    assert _find_list_agents_call(run_output) is True
    assert _find_list_agents_call(RunOutput(status=RunStatus.completed, tools=None)) is False


def test_find_create_team_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("create_team", {"name": "Platform", "description": "Infra team"})],
    )
    call = _find_create_team_call(run_output)
    assert call is not None
    assert call.name == "Platform"
    assert call.description == "Infra team"


def test_find_update_team_call_extracts_agent_ids() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("update_team", {"team": "Platform", "agent_ids": ["a", "b"]})],
    )
    call = _find_update_team_call(run_output)
    assert call is not None
    assert call.team_ref == "Platform"
    assert call.agent_ids == ["a", "b"]


def test_find_delete_team_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("delete_team", {"team": "Platform"})]
    )
    assert _find_delete_team_call(run_output) == "Platform"


def test_find_list_teams_call() -> None:
    run_output = RunOutput(status=RunStatus.completed, tools=[_tool_execution("list_teams", {})])
    assert _find_list_teams_call(run_output) is True


# --- end-to-end dispatch -------------------------------------------------


def _create_controller_agent(
    client: TestClient, headers: dict[str, str], name: str, pattern: str = "go"
) -> str:
    """A keyword rule (matched against "go", present in every triggering
    message below but never in the fake agent reply "ok") rather than
    "always" -- same "keyword, not always" precaution test_channel_tools.py's
    helper takes, so the agent's own reply (re-dispatched recursively,
    FR-5.6) can't re-trigger itself into an infinite tool-call loop."""
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": f"Test controller agent {name}",
            "instructions": "Say something.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": f'["{pattern}"]', "priority": 0}]},
        headers=headers,
    )
    return agent_id


def _create_channel_with_team(client: TestClient, headers: dict[str, str], agent_id: str) -> str:
    from tests.conftest import delete_starter_assistant

    delete_starter_assistant(client, headers)
    team = client.post(
        "/api/v1/teams", json={"name": f"Agent Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"agent-tool-test-{agent_id}"}, headers=headers
    )
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def _fake_run_agent(tool_call: ToolExecution, reply: str = "ok"):
    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[tool_call],
            get_content_as_string=lambda: reply,
        )

    return fake_run_agent


def _trigger(
    client: TestClient,
    headers: dict[str, str],
    channel_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tool_call: ToolExecution,
) -> list[dict[str, Any]]:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent(tool_call))
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": "go do it"}, headers=headers
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]
    return client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=headers).json()


def test_create_agent_creates_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "Creator")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "create_agent")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "create_agent",
            {
                "name": f"NewHire-{controller_id}",
                "description": "A freshly minted teammate agent.",
                "instructions": "Be helpful.",
                "model": "anthropic:claude-3-5-haiku-latest",
            },
        ),
    )
    assert f"created agent @NewHire-{controller_id}" in messages[2]["content"]

    listed = client.get("/api/v1/agents", headers=auth_headers).json()
    created = next(a for a in listed if a["name"] == f"NewHire-{controller_id}")
    assert created["instructions"] == "Be helpful."


def test_create_agent_rejects_short_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "PickyCreator")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "create_agent")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "create_agent",
            {
                "name": "a",
                "description": "A freshly minted teammate agent.",
                "instructions": "Be helpful.",
                "model": "anthropic:claude-3-5-haiku-latest",
            },
        ),
    )
    assert "2-64 characters" in messages[2]["content"]


def test_create_agent_rejects_duplicate_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "DupeCreator")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "create_agent")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "create_agent",
            {
                "name": "DupeCreator",
                "description": "A freshly minted teammate agent.",
                "instructions": "Be helpful.",
                "model": "anthropic:claude-3-5-haiku-latest",
            },
        ),
    )
    assert "already exists" in messages[2]["content"]


def test_create_agent_rejects_short_description(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "DescPicky")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "create_agent")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "create_agent",
            {
                "name": f"ShortDesc-{controller_id}",
                "description": "short",
                "instructions": "Be helpful.",
                "model": "anthropic:claude-3-5-haiku-latest",
            },
        ),
    )
    assert "10-500 characters" in messages[2]["content"]


def test_update_agent_renames_by_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "Renamer")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")
    target_name = f"Target-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent to rename.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent", {"agent": target_name, "name": f"Renamed-{controller_id}"}),
    )
    assert "updated agent" in messages[2]["content"]

    updated = client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).json()
    assert updated["name"] == f"Renamed-{controller_id}"


def test_update_agent_dedupes_repeated_team_id(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model repeating the same team id in `team_ids` shouldn't 500 --
    TeamAgent's primary key is the (team_id, agent_id) pair, so inserting
    the same team twice for one agent would otherwise hit an
    IntegrityError on commit."""
    controller_id = _create_controller_agent(client, auth_headers, "DupeTeamAssigner")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")
    target_name = f"DupeTeamTarget-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent for duplicate team_ids.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()
    team = client.post(
        "/api/v1/teams", json={"name": f"DupeAssignTeam-{controller_id}"}, headers=auth_headers
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent", {"agent": target_name, "team_ids": [team["id"], team["id"]]}
        ),
    )
    assert "updated agent" in messages[2]["content"]

    updated_team = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert updated_team["agent_ids"] == [target["id"]]


def test_update_agent_no_changes_specified_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#310: the target must be a separate, unscoped agent -- the
    controller itself now holds the agents_teams:manage AgentToolScope
    authorize_agent_for_builtin_tool grants it, so a self-targeted call
    would be refused by the new agent_holds_owner_scope gate before this
    "no changes" validation ever runs."""
    controller_id = _create_controller_agent(client, auth_headers, "Indecisive")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")
    target_name = f"IndecisiveTarget-{controller_id}"
    client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent with no scope.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent", {"agent": target_name}),
    )
    assert "didn't specify any changes" in messages[2]["content"]


def test_update_agent_unknown_reference_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "Confused")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent", {"agent": "no-such-agent", "name": "x"}),
    )
    assert "no agent with that id or name" in messages[2]["content"]


def test_delete_agent_removes_it(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "Deleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_agent")
    target_name = f"Doomed-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent to delete.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_agent", {"agent": target_name}),
    )
    assert f"deleted agent @{target_name}" in messages[2]["content"]

    assert client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).status_code == 404


async def test_delete_agent_trigger_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#287: an agent-triggered delete (dispatch/service.py's
    _handle_delete_agent_trigger) must tombstone the same as the HTTP
    delete_agent route does -- the `client` fixture never actually starts
    the sync engine, so a successful delete queues a tombstone retry
    (SyncPendingOutbound.deleted=True) instead of the delete never
    reaching any peer at all."""
    controller_id = _create_controller_agent(client, auth_headers, "SyncDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_agent")
    target_name = f"DoomedSync-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent to delete.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()

    _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_agent", {"agent": target_name}),
    )

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("agent", target["id"]))
        assert pending is not None
        assert pending.deleted is True


def test_delete_agent_refuses_self_delete(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "SelfDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_agent")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_agent", {"agent": "SelfDeleter"}),
    )
    assert "can't delete itself" in messages[2]["content"]
    assert client.get(f"/api/v1/agents/{controller_id}", headers=auth_headers).status_code == 200


def test_update_agent_routing_rules_replaces_rules(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "RuleSetter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, controller_id, "update_agent_routing_rules"
    )
    target_name = f"RuleTarget-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent for routing rules.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent_routing_rules",
            {
                "agent": target_name,
                "rules": [{"rule_type": "keyword", "pattern": ["ping"], "priority": 2}],
            },
        ),
    )
    assert "set 1 routing rule" in messages[2]["content"]

    rules = client.get(f"/api/v1/agents/{target['id']}/routing-rules", headers=auth_headers).json()
    assert len(rules) == 1
    assert rules[0]["rule_type"] == "keyword"
    assert rules[0]["priority"] == 2


def test_update_agent_routing_rules_rejects_invalid_rule(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#310: target must be a separate, unscoped agent -- see
    test_update_agent_no_changes_specified_is_rejected's docstring."""
    controller_id = _create_controller_agent(client, auth_headers, "BadRuleSetter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, controller_id, "update_agent_routing_rules"
    )
    target_name = f"BadRuleTarget-{controller_id}"
    client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent with no scope.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent_routing_rules",
            {"agent": target_name, "rules": [{"rule_type": "not-a-real-type"}]},
        ),
    )
    assert "isn't a valid rule" in messages[2]["content"]


def test_update_agent_peer_preference_sets_and_clears(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#310: target must be a separate, unscoped agent -- see
    test_update_agent_no_changes_specified_is_rejected's docstring."""
    controller_id = _create_controller_agent(client, auth_headers, "PeerSetter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, controller_id, "update_agent_peer_preference"
    )
    target_name = f"PeerTarget-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent with no scope.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent_peer_preference", {"agent": target_name, "capability_tag": "gpu"}
        ),
    )
    assert "gpu" in messages[2]["content"]
    pref = client.get(f"/api/v1/agents/{target['id']}/peer-preference", headers=auth_headers).json()
    assert pref["capability_tag"] == "gpu"

    messages2 = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent_peer_preference", {"agent": target_name}),
    )
    assert "cleared peer preference" in messages2[-1]["content"]
    pref2 = client.get(
        f"/api/v1/agents/{target['id']}/peer-preference", headers=auth_headers
    ).json()
    assert pref2["capability_tag"] is None


async def test_update_agent_peer_preference_clear_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#311: an agent-triggered clear (dispatch/service.py's
    _handle_update_agent_peer_preference_trigger) must tombstone the same
    as the HTTP set_peer_preference route does -- otherwise a peer that
    still has the row never learns the preference was withdrawn.

    #310: target must be a separate, unscoped agent -- see
    test_update_agent_no_changes_specified_is_rejected's docstring."""
    controller_id = _create_controller_agent(client, auth_headers, "PeerClearer")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, controller_id, "update_agent_peer_preference"
    )
    target = _create_unscoped_target(client, auth_headers, f"PeerClearTarget-{controller_id}")

    _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent_peer_preference", {"agent": target["name"], "capability_tag": "gpu"}
        ),
    )
    _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent_peer_preference", {"agent": target["name"]}),
    )

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("agent_peer_preference", target["id"]))
        assert pending is not None
        assert pending.deleted is True


def test_rollback_agent_version_reverts_instructions(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "Rollbacker")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "rollback_agent_version")
    target_name = f"VersionedTarget-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent with version history.",
            "instructions": "Version one instructions.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()
    client.patch(
        f"/api/v1/agents/{target['id']}",
        json={"instructions": "Version two instructions."},
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("rollback_agent_version", {"agent": target_name, "version": 1}),
    )
    assert "rolled back agent" in messages[2]["content"]

    reverted = client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).json()
    assert reverted["instructions"] == "Version one instructions."


def test_rollback_agent_version_unknown_version_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#310: target must be a separate, unscoped agent -- see
    test_update_agent_no_changes_specified_is_rejected's docstring."""
    controller_id = _create_controller_agent(client, auth_headers, "BadRollbacker")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "rollback_agent_version")
    target_name = f"BadRollbackTarget-{controller_id}"
    client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent with no scope.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("rollback_agent_version", {"agent": target_name, "version": 999}),
    )
    assert "doesn't exist" in messages[2]["content"]


def test_list_agents_reports_existing_agents(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "Lister")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)

    messages = _trigger(
        client, auth_headers, channel_id, monkeypatch, _tool_execution("list_agents", {})
    )
    assert "Lister" in messages[2]["content"]


def test_create_team_creates_team(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "TeamCreator")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "create_team")

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "create_team", {"name": f"NewTeam-{controller_id}", "description": "A new team."}
        ),
    )
    assert f"created team 'NewTeam-{controller_id}'" in messages[2]["content"]

    listed = client.get("/api/v1/teams", headers=auth_headers).json()
    assert any(t["name"] == f"NewTeam-{controller_id}" for t in listed)


def test_update_team_renames_and_sets_members(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "TeamUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_team")
    member_name = f"Member-{controller_id}"
    member = client.post(
        "/api/v1/agents",
        json={
            "name": member_name,
            "description": "A team member candidate.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()
    team = client.post(
        "/api/v1/teams", json={"name": f"UpdateTarget-{controller_id}"}, headers=auth_headers
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_team",
            {
                "team": team["id"],
                "name": f"Renamed-Team-{controller_id}",
                "agent_ids": [member_name],
            },
        ),
    )
    assert "updated team" in messages[2]["content"]

    updated = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert updated["name"] == f"Renamed-Team-{controller_id}"
    assert updated["agent_ids"] == [member["id"]]


def test_update_team_dedupes_agent_ids_naming_same_agent_twice(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model referring to the same agent once by name and once by id
    (or just repeating itself) shouldn't 500 -- TeamAgent's primary key
    is the (team_id, agent_id) pair, so inserting the same agent twice
    would otherwise hit an IntegrityError on commit."""
    controller_id = _create_controller_agent(client, auth_headers, "DupeTeamUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_team")
    member_name = f"DupeMember-{controller_id}"
    member = client.post(
        "/api/v1/agents",
        json={
            "name": member_name,
            "description": "A team member candidate.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()
    team = client.post(
        "/api/v1/teams", json={"name": f"DupeUpdateTarget-{controller_id}"}, headers=auth_headers
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_team", {"team": team["id"], "agent_ids": [member_name, member["id"]]}
        ),
    )
    assert "updated team" in messages[2]["content"]

    updated = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert updated["agent_ids"] == [member["id"]]


def test_update_team_ambiguous_name_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "AmbiguousUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_team")
    dup_name = f"Dup-{controller_id}"
    client.post("/api/v1/teams", json={"name": dup_name}, headers=auth_headers)
    client.post("/api/v1/teams", json={"name": dup_name}, headers=auth_headers)

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_team", {"team": dup_name, "name": "whatever"}),
    )
    assert "matches 2 teams" in messages[2]["content"]


def test_delete_team_removes_it(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "TeamDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_team")
    team = client.post(
        "/api/v1/teams", json={"name": f"Doomed-Team-{controller_id}"}, headers=auth_headers
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_team", {"team": team["id"]}),
    )
    assert "deleted team" in messages[2]["content"]
    assert client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).status_code == 404


async def test_delete_team_trigger_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#287: an agent-triggered delete (dispatch/service.py's
    _handle_delete_team_trigger) must tombstone the same as the HTTP
    delete_team route does -- mirrors test_delete_agent_trigger_queues_
    sync_tombstone above."""
    controller_id = _create_controller_agent(client, auth_headers, "TeamSyncDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_team")
    team = client.post(
        "/api/v1/teams", json={"name": f"Doomed-Team-Sync-{controller_id}"}, headers=auth_headers
    ).json()

    _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_team", {"team": team["id"]}),
    )

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("team", team["id"]))
        assert pending is not None
        assert pending.deleted is True


def test_list_teams_reports_members(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "TeamLister")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)

    messages = _trigger(
        client, auth_headers, channel_id, monkeypatch, _tool_execution("list_teams", {})
    )
    assert "Agent Tool Test Team" in messages[2]["content"]
    assert "TeamLister" in messages[2]["content"]


def test_delete_agent_call_ignored_without_agents_teams_manage_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#240: a completed run's `delete_agent` tool call must not delete a
    real agent unless the calling agent actually holds the
    "agents_teams:manage" scope, re-checked fresh at invocation time. See
    test_invite_tools.py's equivalent test for the full rationale --
    delete_agent is this category's highest-consequence tool, so it's the
    one worth a dedicated deny-path regression here."""
    controller_id = _create_controller_agent(client, auth_headers, "UnauthorizedDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    target_name = f"ShouldSurvive-{controller_id}"
    target = client.post(
        "/api/v1/agents",
        json={
            "name": target_name,
            "description": "A target agent that must survive the unauthorized attempt.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    ).json()

    _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_agent", {"agent": target_name}),
    )

    assert client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).status_code == 200


# #310: dispatch/service.py's agent-tool trigger handlers have no live
# session to check a claims.grant == "owner" bypass against -- unlike
# api/agents.py's HTTP routes, where an owner *session* can PATCH a
# custom/MCP tool onto an agent or edit one holding a capability scope,
# whoever is chatting with a controller agent that already holds
# agents_teams:manage could be any invite-grant participant in the
# rivulet. These trigger handlers must refuse outright, the same way
# _mcp_server_requires_owner_to_mutate's register/reconnect/delete
# triggers already do.


def _create_unscoped_target(
    client: TestClient, headers: dict[str, str], name: str
) -> dict[str, Any]:
    return client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "A target agent with no capability scope.",
            "instructions": "Do nothing.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    ).json()


def test_update_agent_tool_ids_with_custom_tool_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors api/agents.py's test_create_agent_with_custom_tool_requires_
    owner_grant at the dispatch layer -- a custom tool's code runs
    unsandboxed the moment it's assigned and called (find_unauthorized_
    tool_assignment's docstring), so update_agent's tool_ids must refuse
    it even though the calling controller legitimately holds
    agents_teams:manage."""
    controller_id = _create_controller_agent(client, auth_headers, "CustomToolAssigner")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")
    target = _create_unscoped_target(client, auth_headers, f"CustomToolTarget-{controller_id}")
    custom_tool_name = f"custom_tool_{controller_id.replace('-', '_')}"
    custom_tool = client.post(
        "/api/v1/tools",
        json={"name": custom_tool_name, "description": "Does a thing."},
        headers=auth_headers,
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent", {"agent": target["name"], "tool_ids": [custom_tool["id"]]}),
    )
    assert "requires a live owner session" in messages[2]["content"]
    assert (
        client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).json()["instructions"]
        == "Do nothing."
    )


async def test_update_agent_tool_ids_with_mcp_tool_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors api/agents.py's test_create_agent_with_mcp_tool_requires_
    owner_grant at the dispatch layer -- an MCP tool resolves with the
    owner's stored headers/env, so it needs the same refusal a custom
    tool gets."""
    controller_id = _create_controller_agent(client, auth_headers, "McpToolAssigner")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")
    target = _create_unscoped_target(client, auth_headers, f"McpToolTarget-{controller_id}")
    async with session_scope() as db:
        server = MCPServer(name=f"test-server-{controller_id}", url="http://localhost:9999/mcp")
        db.add(server)
        await db.commit()
        tool_row = Tool(
            name=f"remote_tool-{controller_id}",
            description="An MCP tool.",
            tool_type="mcp",
            mcp_server_id=server.id,
            mcp_tool_name="remote_tool",
        )
        db.add(tool_row)
        await db.commit()
        mcp_tool_id = tool_row.id

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent", {"agent": target["name"], "tool_ids": [mcp_tool_id]}),
    )
    assert "requires a live owner session" in messages[2]["content"]


def test_update_agent_target_holding_owner_scope_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confused-deputy case #310 closes: a controller with a
    legitimate agents_teams:manage grant must not be able to rewrite an
    agent that separately holds an owner-granted AgentToolScope, even
    though the controller never touched that scope itself."""
    controller_id = _create_controller_agent(client, auth_headers, "PrivRewriter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_agent")
    target = _create_unscoped_target(client, auth_headers, f"PrivTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{target['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_agent", {"agent": target["name"], "instructions": "Be evil."}),
    )
    assert "holds a capability scope" in messages[2]["content"]
    assert (
        client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).json()["instructions"]
        == "Do nothing."
    )


def test_delete_agent_target_holding_owner_scope_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "PrivDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_agent")
    target = _create_unscoped_target(client, auth_headers, f"PrivDeleteTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{target['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_agent", {"agent": target["name"]}),
    )
    assert "holds a capability scope" in messages[2]["content"]
    assert client.get(f"/api/v1/agents/{target['id']}", headers=auth_headers).status_code == 200


def test_update_agent_routing_rules_target_holding_owner_scope_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "PrivRuleSetter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, controller_id, "update_agent_routing_rules"
    )
    target = _create_unscoped_target(client, auth_headers, f"PrivRuleTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{target['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent_routing_rules",
            {"agent": target["name"], "rules": [{"rule_type": "always", "priority": 0}]},
        ),
    )
    assert "holds a capability scope" in messages[2]["content"]


def test_update_agent_peer_preference_target_holding_owner_scope_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "PrivPeerSetter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(
        client, auth_headers, controller_id, "update_agent_peer_preference"
    )
    target = _create_unscoped_target(client, auth_headers, f"PrivPeerTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{target['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_agent_peer_preference", {"agent": target["name"], "capability_tag": "gpu"}
        ),
    )
    assert "holds a capability scope" in messages[2]["content"]
    pref = client.get(f"/api/v1/agents/{target['id']}/peer-preference", headers=auth_headers).json()
    assert pref["capability_tag"] is None


def test_rollback_agent_version_target_holding_owner_scope_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller_id = _create_controller_agent(client, auth_headers, "PrivRollbacker")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "rollback_agent_version")
    target = _create_unscoped_target(client, auth_headers, f"PrivRollbackTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{target['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("rollback_agent_version", {"agent": target["name"], "version": 1}),
    )
    assert "holds a capability scope" in messages[2]["content"]


# #387: leftover of #353/#326 at the dispatch layer -- update_team/
# delete_team have no live session to check a claims.grant == "owner"
# bypass against. Adding or dropping a scoped member, or deleting a team
# that holds one, must refuse the same way HTTP already does.


def test_update_team_adding_scoped_member_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387 / #326: a controller with agents_teams:manage must not be
    able to add an agent that holds an owner-granted capability scope."""
    controller_id = _create_controller_agent(client, auth_headers, "ScopedAdder")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_team")
    scoped = _create_unscoped_target(client, auth_headers, f"ScopedAddTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{scoped['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )
    team = client.post(
        "/api/v1/teams", json={"name": f"AddScopedTarget-{controller_id}"}, headers=auth_headers
    ).json()

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_team",
            {"team": team["id"], "name": "Should-Not-Rename", "agent_ids": [scoped["name"]]},
        ),
    )
    assert "holds a capability scope" in messages[2]["content"]
    updated = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert updated["name"] == f"AddScopedTarget-{controller_id}"
    assert updated["agent_ids"] == []


def test_update_team_dropping_scoped_member_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387 / #353: full-replace membership that omits a scoped member
    severs the @mention path the owner set up."""
    controller_id = _create_controller_agent(client, auth_headers, "ScopedDropper")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_team")
    scoped = _create_unscoped_target(client, auth_headers, f"ScopedDropTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{scoped['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )
    team = client.post(
        "/api/v1/teams", json={"name": f"DropScopedTarget-{controller_id}"}, headers=auth_headers
    ).json()
    patched = client.patch(
        f"/api/v1/teams/{team['id']}", json={"agent_ids": [scoped["id"]]}, headers=auth_headers
    )
    assert patched.status_code == 200, patched.text

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("update_team", {"team": team["id"], "agent_ids": []}),
    )
    assert "holds a capability scope" in messages[2]["content"]
    updated = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert updated["agent_ids"] == [scoped["id"]]


def test_update_team_keeping_scoped_member_still_allowed(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP update_team lets a non-owner keep/reorder an existing scoped
    member; the tool must match so a name-only edit isn't blocked."""
    controller_id = _create_controller_agent(client, auth_headers, "ScopedKeeper")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "update_team")
    scoped = _create_unscoped_target(client, auth_headers, f"ScopedKeepTarget-{controller_id}")
    client.put(
        f"/api/v1/agents/{scoped['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )
    team = client.post(
        "/api/v1/teams", json={"name": f"KeepScopedTarget-{controller_id}"}, headers=auth_headers
    ).json()
    patched = client.patch(
        f"/api/v1/teams/{team['id']}", json={"agent_ids": [scoped["id"]]}, headers=auth_headers
    )
    assert patched.status_code == 200, patched.text

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution(
            "update_team",
            {
                "team": team["id"],
                "name": f"Renamed-KeepScoped-{controller_id}",
                "agent_ids": [scoped["id"]],
            },
        ),
    )
    assert "updated team" in messages[2]["content"]
    updated = client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).json()
    assert updated["name"] == f"Renamed-KeepScoped-{controller_id}"
    assert updated["agent_ids"] == [scoped["id"]]


def test_delete_team_holding_scoped_agent_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387 / #353: deleting the whole team is at least as disruptive as
    dropping the scoped member."""
    controller_id = _create_controller_agent(client, auth_headers, "ScopedTeamDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, controller_id)
    authorize_agent_for_builtin_tool(client, auth_headers, controller_id, "delete_team")
    scoped = _create_unscoped_target(
        client, auth_headers, f"ScopedTeamDeleteTarget-{controller_id}"
    )
    client.put(
        f"/api/v1/agents/{scoped['id']}/tool-scopes",
        json={"scopes": ["channels:manage"]},
        headers=auth_headers,
    )
    team = client.post(
        "/api/v1/teams", json={"name": f"Doomed-Scoped-{controller_id}"}, headers=auth_headers
    ).json()
    patched = client.patch(
        f"/api/v1/teams/{team['id']}", json={"agent_ids": [scoped["id"]]}, headers=auth_headers
    )
    assert patched.status_code == 200, patched.text

    messages = _trigger(
        client,
        auth_headers,
        channel_id,
        monkeypatch,
        _tool_execution("delete_team", {"team": team["id"]}),
    )
    assert "holds an agent with a capability scope" in messages[2]["content"]
    assert client.get(f"/api/v1/teams/{team['id']}", headers=auth_headers).status_code == 200
