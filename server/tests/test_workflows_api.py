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

from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope


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


def _add_workflow_node(
    client: TestClient, headers: dict[str, str], workflow_id: str, name: str, child_workflow_id: str
) -> str:
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": name, "node_type": "workflow", "child_workflow_id": child_workflow_id},
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


def test_set_and_clear_on_failure_workflow_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#94 layer 2: on_failure_workflow_id is settable, and -- unlike every
    other WorkflowUpdate field -- explicitly clearable back to null."""
    workflow_id = _create_workflow(client, auth_headers, "flaky")
    fixer_id = _create_workflow(client, auth_headers, "fixer")
    fixer_node_id = _add_transform_node(client, auth_headers, fixer_id, "recover", "{input}")
    _connect(client, auth_headers, fixer_id, None, fixer_node_id)
    _publish_workflow(client, auth_headers, fixer_id)  # #292: remediation target must be published

    set_resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_failure_workflow_id": fixer_id},
        headers=auth_headers,
    )
    assert set_resp.status_code == 200, set_resp.text
    assert set_resp.json()["on_failure_workflow_id"] == fixer_id

    got = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers)
    assert got.json()["on_failure_workflow_id"] == fixer_id

    clear_resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_failure_workflow_id": None},
        headers=auth_headers,
    )
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["on_failure_workflow_id"] is None


def test_on_failure_workflow_id_allows_self_reference(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Unlike child_workflow_id (#85), a workflow retrying itself once on
    failure is a legitimate shape, not rejected -- see #94's depth-1 cap."""
    workflow_id = _create_workflow(client, auth_headers, "self-retry")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "step", "{input}")
    _connect(client, auth_headers, workflow_id, None, node_id)
    _publish_workflow(client, auth_headers, workflow_id)  # #292: remediation target must be published
    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_failure_workflow_id": workflow_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["on_failure_workflow_id"] == workflow_id


def test_on_failure_workflow_id_rejects_unknown_workflow(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "flaky2")
    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_failure_workflow_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_on_failure_workflow_id_rejects_unpublished_workflow(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#292: a draft can't be wired up as a remediation target -- unlike
    child_workflow_id (a graph-building reference the runtime re-checks
    later), a remediation run fires unattended off a finalize, so this
    endpoint refuses to save one pointed at an unpublished workflow."""
    workflow_id = _create_workflow(client, auth_headers, "flaky3")
    draft_fixer_id = _create_workflow(client, auth_headers, "draft-fixer")
    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_failure_workflow_id": draft_fixer_id},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "isn't published" in resp.text


def test_set_and_clear_on_call_agent_id(client: TestClient, auth_headers: dict[str, str]) -> None:
    """#94 layer 3: on_call_agent_id, like on_failure_workflow_id, is
    settable and explicitly clearable back to null via PATCH."""
    workflow_id = _create_workflow(client, auth_headers, "flaky3")
    oncall_id = _create_agent(client, auth_headers, "Oncall1")

    set_resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_call_agent_id": oncall_id},
        headers=auth_headers,
    )
    assert set_resp.status_code == 200, set_resp.text
    assert set_resp.json()["on_call_agent_id"] == oncall_id

    clear_resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_call_agent_id": None},
        headers=auth_headers,
    )
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["on_call_agent_id"] is None


def test_on_call_agent_id_rejects_unknown_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "flaky4")
    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_call_agent_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_on_call_agent_is_mentioned_and_responds_when_a_run_fails(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#94 layer 3: a failed run @mentions Workflow.on_call_agent_id
    through the ordinary dispatch path (not a separate notification
    mechanism) -- the alert message and the on-call agent's reply both
    land in the same rivulet the failure happened in."""
    workflow_id = _create_workflow(client, auth_headers, "flaky-service")
    doomed_agent_id = _create_agent(client, auth_headers, "DoomedWorker")
    node_created = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "call", "node_type": "agent", "agent_id": doomed_agent_id},
        headers=auth_headers,
    )
    assert node_created.status_code == 201, node_created.text
    node_id = node_created.json()["id"]
    _connect(client, auth_headers, workflow_id, None, node_id)
    _publish_workflow(client, auth_headers, workflow_id)

    oncall_id = _create_agent(client, auth_headers, "Oncall2")
    team = client.post("/api/v1/teams", json={"name": "Oncall Team"}, headers=auth_headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [oncall_id]}, headers=auth_headers)
    channel_id = _create_channel(client, auth_headers, "flaky-channel")
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=auth_headers)

    set_resp = client.patch(
        f"/api/v1/workflows/{workflow_id}",
        json={"on_call_agent_id": oncall_id},
        headers=auth_headers,
    )
    assert set_resp.status_code == 200, set_resp.text

    async def doomed_run_agent(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("upstream is down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", doomed_run_agent)

    async def oncall_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[],
            get_content_as_string=lambda: "On it, investigating.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", oncall_run_agent)

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/flaky-service go"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    contents = [(m["sender_type"], m["sender_name"], m["content"]) for m in messages]
    assert any(
        sender_type == "system" and "@Oncall2" in content and "/flaky-service" in content
        for sender_type, _, content in contents
    )
    assert ("agent", "Oncall2", "On it, investigating.") in contents


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


def test_create_workflow_node_requires_child_workflow_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "needs-child")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "invoke", "node_type": "workflow"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_workflow_node_rejects_self_reference(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "self-ref")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "invoke-self", "node_type": "workflow", "child_workflow_id": workflow_id},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_workflow_node_rejects_unknown_child_workflow(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "dangling-ref")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={
            "name": "invoke-missing",
            "node_type": "workflow",
            "child_workflow_id": "does-not-exist",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_create_workflow_node_round_trips_child_workflow_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    child_id = _create_workflow(client, auth_headers, "child-flow")
    parent_id = _create_workflow(client, auth_headers, "parent-flow")
    node_id = _add_workflow_node(client, auth_headers, parent_id, "invoke-child", child_id)

    got = client.get(f"/api/v1/workflows/{parent_id}/nodes", headers=auth_headers).json()
    node = next(n for n in got if n["id"] == node_id)
    assert node["child_workflow_id"] == child_id


def test_node_position_round_trips_through_create_and_update(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "positioned")
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={
            "name": "step",
            "node_type": "transform",
            "config": {"template": "{input}"},
            "position_x": 120.0,
            "position_y": 40.0,
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["position_x"] == 120.0
    assert created.json()["position_y"] == 40.0
    node_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/workflows/{workflow_id}/nodes/{node_id}",
        json={"position_x": 300.0, "position_y": 90.0},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["position_x"] == 300.0
    assert updated.json()["position_y"] == 90.0


def test_node_without_position_gets_auto_layout_fallback_on_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#194: nodes created without an explicit position (e.g. every node
    saved before the canvas existed) still get sensible coordinates from
    the GET /nodes response instead of stacking at the origin."""
    workflow_id = _create_workflow(client, auth_headers, "unpositioned")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    _connect(client, auth_headers, workflow_id, a, b)

    nodes = client.get(f"/api/v1/workflows/{workflow_id}/nodes", headers=auth_headers).json()
    node_a = next(n for n in nodes if n["id"] == a)
    node_b = next(n for n in nodes if n["id"] == b)
    assert node_a["position_x"] is not None and node_a["position_y"] is not None
    assert node_b["position_x"] is not None and node_b["position_y"] is not None
    assert node_a["position_x"] != node_b["position_x"]


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


def test_update_connection_sets_and_clears_condition_json(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#198: the canvas's edge inspector edits an existing connection's
    condition in place rather than deleting and recreating it."""
    workflow_id = _create_workflow(client, auth_headers, "editable-condition")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": a, "to_node_id": b},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]
    assert created.json()["condition_json"] is None

    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}/connections/{connection_id}",
        json={"condition_json": {"not_contains": "spam"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["condition_json"] == {"not_contains": "spam"}

    cleared = client.patch(
        f"/api/v1/workflows/{workflow_id}/connections/{connection_id}",
        json={"condition_json": None},
        headers=auth_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["condition_json"] is None


def test_update_connection_rejects_malformed_condition_json(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "bad-update-condition")
    a = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    b = _add_transform_node(client, auth_headers, workflow_id, "b", "{input}")
    _connect(client, auth_headers, workflow_id, None, a)
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": a, "to_node_id": b},
        headers=auth_headers,
    )
    connection_id = created.json()["id"]

    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}/connections/{connection_id}",
        json={"condition_json": {"contains": "x", "extra": 1}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_update_connection_404_for_unknown_connection(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "no-such-connection")
    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}/connections/does-not-exist",
        json={"condition_json": {"contains": "x"}},
        headers=auth_headers,
    )
    assert resp.status_code == 404


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


async def test_delete_workflow_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#287: the `client` fixture never actually starts the sync engine, so
    a successful delete queues a tombstone retry (SyncPendingOutbound.
    deleted=True) instead of the delete never reaching any peer at all --
    mirrors test_teams_api.py's equivalent for delete_team."""
    workflow_id = _create_workflow(client, auth_headers, "doomed-sync")

    resp = client.delete(f"/api/v1/workflows/{workflow_id}", headers=auth_headers)
    assert resp.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("workflow", workflow_id))
        assert pending is not None
        assert pending.deleted is True


async def test_delete_node_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#287: a lone node delete (workflow left in place) needs its own
    tombstone -- unlike delete_workflow's cascade, there's no parent
    tombstone for a peer's apply to cascade this node away with."""
    workflow_id = _create_workflow(client, auth_headers, "doomed-node-sync")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")

    resp = client.delete(f"/api/v1/workflows/{workflow_id}/nodes/{node_id}", headers=auth_headers)
    assert resp.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("workflow_node", node_id))
        assert pending is not None
        assert pending.deleted is True


async def test_delete_connection_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#287: same reasoning as test_delete_node_queues_sync_tombstone."""
    workflow_id = _create_workflow(client, auth_headers, "doomed-connection-sync")
    node_id = _add_transform_node(client, auth_headers, workflow_id, "a", "{input}")
    _connect(client, auth_headers, workflow_id, None, node_id)
    connections = client.get(
        f"/api/v1/workflows/{workflow_id}/connections", headers=auth_headers
    ).json()
    connection_id = connections[0]["id"]

    resp = client.delete(
        f"/api/v1/workflows/{workflow_id}/connections/{connection_id}", headers=auth_headers
    )
    assert resp.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("workflow_connection", connection_id))
        assert pending is not None
        assert pending.deleted is True


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


def test_list_failed_runs_empty_when_none_failed(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/v1/workflows/runs/failed", headers=auth_headers).json() == []


def test_list_failed_runs_spans_workflows_and_excludes_completed(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#94: the cross-workflow observability endpoint -- a failed run shows
    up here regardless of which workflow it belongs to, joined with the
    workflow's name and the rivulet's channel_id (neither implied by the
    URL the way they are for the per-workflow /runs endpoint), while a
    completed run from a different workflow doesn't."""
    ok_workflow_id = _create_workflow(client, auth_headers, "healthy-flow")
    ok_node_id = _add_transform_node(client, auth_headers, ok_workflow_id, "echo", "{input}")
    _connect(client, auth_headers, ok_workflow_id, None, ok_node_id)
    _publish_workflow(client, auth_headers, ok_workflow_id)

    doomed_workflow_id = _create_workflow(client, auth_headers, "doomed-flow")
    agent_id = _create_agent(client, auth_headers, "DoomedAgent")
    added = client.post(
        f"/api/v1/workflows/{doomed_workflow_id}/nodes",
        json={"name": "call-agent", "node_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    )
    assert added.status_code == 201, added.text
    doomed_node_id = added.json()["id"]
    _connect(client, auth_headers, doomed_workflow_id, None, doomed_node_id)
    _publish_workflow(client, auth_headers, doomed_workflow_id)

    async def always_fails(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider is down")

    monkeypatch.setattr("rivulets.workflows.nodes.run_agent", always_fails)

    channel_id = _create_channel(client, auth_headers, "obs-channel")

    ok_rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/healthy-flow hi"},
        headers=auth_headers,
    )
    assert ok_rivulet.status_code == 201, ok_rivulet.text

    failed_rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "/doomed-flow hi"},
        headers=auth_headers,
    )
    assert failed_rivulet.status_code == 201, failed_rivulet.text
    failed_rivulet_id = failed_rivulet.json()["id"]

    failed = client.get("/api/v1/workflows/runs/failed", headers=auth_headers)
    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert len(body) == 1
    assert body[0]["workflow_id"] == doomed_workflow_id
    assert body[0]["workflow_name"] == "doomed-flow"
    assert body[0]["channel_id"] == channel_id
    assert body[0]["rivulet_id"] == failed_rivulet_id
    assert body[0]["status"] == "failed"
    assert "provider is down" in (body[0]["error_message"] or "")
