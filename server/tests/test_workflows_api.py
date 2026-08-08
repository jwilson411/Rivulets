"""#24: workflow definition CRUD (api/workflows.py), the /​{name} <input>
slash-command trigger (api/rivulets.py, workflows/trigger.py), and the
run_workflow builtin tool's agent-triggered path (dispatch/service.py's
_handle_run_workflow_trigger) — HTTP-level coverage, mirroring
test_invites_api.py's CRUD style and test_handoff.py's monkeypatched
run_agent style for the agent-triggered case.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.base import RunStatus
from fastapi.testclient import TestClient


def _create_workflow(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/workflows", json={"name": name, "description": "test workflow"}, headers=headers
    )
    assert created.status_code == 201, created.text
    workflow_id: str = created.json()["id"]
    return workflow_id


def _add_transform_node(
    client: TestClient, headers: dict[str, str], workflow_id: str, name: str, template: str
) -> str:
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": name, "node_type": "transform", "config": {"template": template}},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    node_id: str = created.json()["id"]
    return node_id


def _publish_workflow(client: TestClient, headers: dict[str, str], workflow_id: str) -> None:
    resp = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=headers)
    assert resp.status_code == 200, resp.text


def _add_human_input_node(
    client: TestClient, headers: dict[str, str], workflow_id: str, name: str
) -> str:
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": name, "node_type": "human_input"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    node_id: str = created.json()["id"]
    return node_id


def _connect(
    client: TestClient,
    headers: dict[str, str],
    workflow_id: str,
    from_node_id: str | None,
    to_node_id: str,
) -> None:
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": from_node_id, "to_node_id": to_node_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


def test_create_and_get_workflow(client: TestClient, auth_headers: dict[str, str]) -> None:
    workflow_id = _create_workflow(client, auth_headers, "release-checklist")
    got = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["name"] == "release-checklist"


def test_create_workflow_rejects_duplicate_name(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create_workflow(client, auth_headers, "dup-name")
    resp = client.post("/api/v1/workflows", json={"name": "dup-name"}, headers=auth_headers)
    assert resp.status_code == 409


def test_create_workflow_rejects_invalid_name_shape(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post("/api/v1/workflows", json={"name": "Not Valid!"}, headers=auth_headers)
    assert resp.status_code == 422


def test_create_agent_node_requires_agent_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "needs-agent")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "step", "node_type": "agent"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_node_rejects_unknown_node_type(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "bad-type")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "step", "node_type": "teleport"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_second_outbound_connection_from_same_node_is_allowed(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#81: a node may fan out to multiple outbound edges now that the
    engine walks a real graph — only the entry point stays singular (see
    test_second_entry_connection_is_conflict)."""
    workflow_id = _create_workflow(client, auth_headers, "branchy")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    c = _add_transform_node(client, auth_headers, workflow_id, "c", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    _connect(client, auth_headers, workflow_id, a, b)
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": a, "to_node_id": c},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    connections = client.get(
        f"/api/v1/workflows/{workflow_id}/connections", headers=auth_headers
    ).json()
    outbound_from_a = [c for c in connections if c["from_node_id"] == a]
    assert len(outbound_from_a) == 2


def test_connection_condition_json_round_trips(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "conditioned")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": a, "to_node_id": b, "condition_json": {"contains": "urgent"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["condition_json"] == {"contains": "urgent"}


def test_connection_rejects_malformed_condition_json(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "bad-condition")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": a, "to_node_id": b, "condition_json": {"contains": "x", "extra": 1}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_second_entry_connection_is_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "two-starts")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": None, "to_node_id": b},
        headers=auth_headers,
    )
    assert resp.status_code == 409


def test_delete_workflow_cascades_nodes_and_connections(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "throwaway")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    _connect(client, auth_headers, workflow_id, None, node_id)

    resp = client.delete(f"/api/v1/workflows/{workflow_id}", headers=auth_headers)
    assert resp.status_code == 204
    assert client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).status_code == 404


def _create_channel(client: TestClient, headers: dict[str, str], name: str) -> str:
    channel = client.post("/api/v1/channels", json={"name": name}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id: str = channel.json()["id"]
    return channel_id


def test_slash_command_triggers_workflow_instead_of_dispatch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "shout")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "shout", "{input}!!!")
    _connect(client, auth_headers, workflow_id, None, node_id)
    _publish_workflow(client, auth_headers, workflow_id)

    channel_id = _create_channel(client, auth_headers, "wf-channel")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/shout hello there"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    contents = [(m["sender_type"], m["content_type"], m["content"]) for m in messages]
    assert contents[0] == ("human", "text", "/shout hello there")
    assert contents[1][1] == "workflow_step"
    assert contents[2] == ("system", "text", "hello there!!!")

    runs = client.get(f"/api/v1/workflows/{workflow_id}/runs", headers=auth_headers).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["triggered_by"] == "human"


def test_reply_to_a_paused_workflow_resumes_it_instead_of_dispatching(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#83: a 'human_input' node pauses the run and the rivulet; the next
    message posted there is treated as the reply (workflows/trigger.py's
    find_awaiting_workflow_run), not run through ordinary dispatch."""
    workflow_id = _create_workflow(client, auth_headers, "onboard")
    ask_id = _add_human_input_node(client, auth_headers, workflow_id, "ask")
    echo_id = _add_transform_node(client, auth_headers, workflow_id, "echo", "confirmed: {input}")
    _connect(client, auth_headers, workflow_id, None, ask_id)
    _connect(client, auth_headers, workflow_id, ask_id, echo_id)
    _publish_workflow(client, auth_headers, workflow_id)

    channel_id = _create_channel(client, auth_headers, "onboard-channel")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/onboard start"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    got_rivulet = client.get(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers).json()
    assert got_rivulet["status"] == "paused"

    runs = client.get(f"/api/v1/workflows/{workflow_id}/runs", headers=auth_headers).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "awaiting_human"

    reply = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages", json={"content": "yes"}, headers=auth_headers
    )
    assert reply.status_code == 201, reply.text

    got_rivulet = client.get(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers).json()
    assert got_rivulet["status"] == "active"

    runs = client.get(f"/api/v1/workflows/{workflow_id}/runs", headers=auth_headers).json()
    assert runs[0]["status"] == "completed"

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    contents = [(m["sender_type"], m["content_type"], m["content"]) for m in messages]
    # The reply is the literal human message, not run through dispatch --
    # no extra agent messages appear from it.
    assert ("human", "text", "yes") in contents
    assert ("system", "text", "confirmed: yes") in contents


def test_resume_rivulet_refuses_while_a_workflow_is_awaiting_human(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """A workflow's pause isn't clearable via the generic /resume endpoint
    -- unlike a loop-guard pause, there's no reply value to supply without
    an actual message."""
    workflow_id = _create_workflow(client, auth_headers, "gatekeeper")
    ask_id = _add_human_input_node(client, auth_headers, workflow_id, "ask")
    _connect(client, auth_headers, workflow_id, None, ask_id)
    _publish_workflow(client, auth_headers, workflow_id)

    channel_id = _create_channel(client, auth_headers, "gatekeeper-channel")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/gatekeeper start"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    resp = client.post(f"/api/v1/rivulets/{rivulet_id}/resume", headers=auth_headers)
    assert resp.status_code == 400

    got_rivulet = client.get(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers).json()
    assert got_rivulet["status"] == "paused"


def test_publish_rejects_a_workflow_with_no_entry_point(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "empty-flow")
    resp = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers)
    assert resp.status_code == 400


def test_publish_and_unpublish_round_trip(client: TestClient, auth_headers: dict[str, str]) -> None:
    workflow_id = _create_workflow(client, auth_headers, "toggle-flow")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "step", "{input}")
    _connect(client, auth_headers, workflow_id, None, node_id)

    created = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).json()
    assert created["published"] is False

    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers)
    assert published.status_code == 200, published.text
    assert published.json()["published"] is True

    unpublished = client.post(f"/api/v1/workflows/{workflow_id}/unpublish", headers=auth_headers)
    assert unpublished.status_code == 200, unpublished.text
    assert unpublished.json()["published"] is False


def test_unpublished_workflow_slash_command_falls_through_to_dispatch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#84: a workflow that's never been published isn't triggerable by
    name yet -- the slash command falls through to ordinary dispatch,
    same as a name that doesn't exist at all (see
    test_slash_shaped_message_with_no_matching_workflow_falls_through)."""
    workflow_id = _create_workflow(client, auth_headers, "draft-only")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "step", "{input}!!!")
    _connect(client, auth_headers, workflow_id, None, node_id)
    # deliberately not published

    channel_id = _create_channel(client, auth_headers, "draft-channel")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/draft-only hello"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert len(messages) == 1
    assert messages[0]["content"] == "/draft-only hello"

    runs = client.get(f"/api/v1/workflows/{workflow_id}/runs", headers=auth_headers).json()
    assert runs == []


def test_resume_uses_the_graph_snapshot_from_when_the_run_started(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#84: a workflow edited in the builder while a run sits paused on a
    'human_input' node shouldn't change what that paused run does on
    resume -- it keeps walking the graph as it existed when the run
    started, not whatever the definition looks like now."""
    workflow_id = _create_workflow(client, auth_headers, "editable-mid-pause")
    ask_id = _add_human_input_node(client, auth_headers, workflow_id, "ask")
    original_echo_id = _add_transform_node(
        client, auth_headers, workflow_id, "original-echo", "original: {input}"
    )
    _connect(client, auth_headers, workflow_id, None, ask_id)
    _connect(client, auth_headers, workflow_id, ask_id, original_echo_id)
    _publish_workflow(client, auth_headers, workflow_id)

    channel_id = _create_channel(client, auth_headers, "editable-channel")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/editable-mid-pause start"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    # Edit the live graph while the run is paused: swap in a brand new
    # step after "ask" instead of the original one.
    new_echo_id = _add_transform_node(client, auth_headers, workflow_id, "new-echo", "NEW: {input}")
    connections = client.get(
        f"/api/v1/workflows/{workflow_id}/connections", headers=auth_headers
    ).json()
    old_edge = next(c for c in connections if c["from_node_id"] == ask_id)
    client.delete(
        f"/api/v1/workflows/{workflow_id}/connections/{old_edge['id']}", headers=auth_headers
    )
    _connect(client, auth_headers, workflow_id, ask_id, new_echo_id)

    reply = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages", json={"content": "go"}, headers=auth_headers
    )
    assert reply.status_code == 201, reply.text

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    contents = [m["content"] for m in messages]
    # The paused run's frozen snapshot still points at the original step,
    # not the one swapped in while it was paused.
    assert "original: go" in contents
    assert "NEW: go" not in contents


def test_slash_shaped_message_with_no_matching_workflow_falls_through(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel_id = _create_channel(client, auth_headers, "no-match-channel")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/nonexistent-workflow do something"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    # No team on this channel, so normal dispatch matches nobody -- just
    # the literal human message, proving it was NOT intercepted as a
    # workflow trigger.
    assert len(messages) == 1
    assert messages[0]["content"] == "/nonexistent-workflow do something"


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


def _create_agent(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": f"Test agent {name} with a long enough description.",
            "instructions": "Say something.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "mention_only", "pattern": "", "priority": 0}]},
        headers=headers,
    )
    return agent_id


def test_agent_can_trigger_workflow_via_run_workflow_tool(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "escalate")
    node_id = _add_transform_node(
        client, auth_headers, workflow_id, "escalate", "ESCALATED: {input}"
    )
    _connect(client, auth_headers, workflow_id, None, node_id)
    _publish_workflow(client, auth_headers, workflow_id)

    triggerer_id = _create_agent(client, auth_headers, "Triggerer")

    team = client.post("/api/v1/teams", json={"name": "Trigger Team"}, headers=auth_headers)
    team_id = team.json()["id"]
    client.patch(
        f"/api/v1/teams/{team_id}", json={"agent_ids": [triggerer_id]}, headers=auth_headers
    )
    channel_id = _create_channel(client, auth_headers, "trigger-channel")
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=auth_headers)

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[
                _tool_execution(
                    "run_workflow", {"workflow_name": "escalate", "workflow_input": "server down"}
                )
            ],
            get_content_as_string=lambda: "Kicking off the escalate workflow.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@Triggerer help"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    contents = [m["content"] for m in messages]
    assert "Kicking off the escalate workflow." in contents
    assert "ESCALATED: server down" in contents

    runs = client.get(f"/api/v1/workflows/{workflow_id}/runs", headers=auth_headers).json()
    assert len(runs) == 1
    assert runs[0]["triggered_by"] == "agent"
    assert runs[0]["triggered_by_id"] == triggerer_id


def test_agent_triggering_unknown_workflow_is_skipped_gracefully(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    triggerer_id = _create_agent(client, auth_headers, "Triggerer2")
    team = client.post("/api/v1/teams", json={"name": "Trigger Team 2"}, headers=auth_headers)
    team_id = team.json()["id"]
    client.patch(
        f"/api/v1/teams/{team_id}", json={"agent_ids": [triggerer_id]}, headers=auth_headers
    )
    channel_id = _create_channel(client, auth_headers, "trigger-channel-2")
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=auth_headers)

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[
                _tool_execution(
                    "run_workflow", {"workflow_name": "does-not-exist", "workflow_input": "x"}
                )
            ],
            get_content_as_string=lambda: "Trying to run it.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@Triggerer2 help"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
