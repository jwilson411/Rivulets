"""#192: agent-facing workflow definition management -- the
create_workflow/update_workflow/delete_workflow/publish_workflow/
unpublish_workflow/list_workflows tools (tools/builtin/workflows.py) and
their detection + handling in dispatch/service.py. Mirrors
test_channel_tools.py's/test_mcp_server_tools.py's style: agentos.run_agent
is monkeypatched to hand back a RunOutput with the tool call already
baked in, since real tool-call detection by a live model isn't something
a fake API key can produce.
"""

from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope
from rivulets.dispatch.service import (
    _find_create_workflow_call,  # pyright: ignore[reportPrivateUsage]
    _find_delete_workflow_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_workflows_call,  # pyright: ignore[reportPrivateUsage]
    _find_publish_workflow_call,  # pyright: ignore[reportPrivateUsage]
    _find_unpublish_workflow_call,  # pyright: ignore[reportPrivateUsage]
    _find_update_workflow_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.workflows import (
    create_workflow,
    delete_workflow,
    list_workflows,
    publish_workflow,
    unpublish_workflow,
    update_workflow,
)
from tests.conftest import authorize_agent_for_builtin_tool  # pyright: ignore[reportMissingImports]


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


# --- tool entrypoints -------------------------------------------------


def test_create_workflow_tool_returns_confirmation_string() -> None:
    assert create_workflow.entrypoint is not None
    assert "release-checklist" in create_workflow.entrypoint(name="release-checklist")


def test_update_workflow_tool_returns_confirmation_string() -> None:
    assert update_workflow.entrypoint is not None
    assert "release-checklist" in update_workflow.entrypoint(
        workflow="release-checklist", name="release-checklist-2026"
    )


def test_delete_workflow_tool_returns_confirmation_string() -> None:
    assert delete_workflow.entrypoint is not None
    assert "release-checklist" in delete_workflow.entrypoint(workflow="release-checklist")


def test_publish_workflow_tool_returns_confirmation_string() -> None:
    assert publish_workflow.entrypoint is not None
    assert "release-checklist" in publish_workflow.entrypoint(workflow="release-checklist")


def test_unpublish_workflow_tool_returns_confirmation_string() -> None:
    assert unpublish_workflow.entrypoint is not None
    assert "release-checklist" in unpublish_workflow.entrypoint(workflow="release-checklist")


def test_list_workflows_tool_returns_confirmation_string() -> None:
    assert list_workflows.entrypoint is not None
    assert "workflow" in list_workflows.entrypoint().lower()


# --- tool-call parsers --------------------------------------------------


def test_find_create_workflow_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "create_workflow", {"name": "release-checklist", "description": "Ship it"}
            )
        ],
    )
    call = _find_create_workflow_call(run_output)
    assert call is not None
    assert call.name == "release-checklist"
    assert call.description == "Ship it"


def test_find_create_workflow_call_missing_name_returns_none() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("create_workflow", {})]
    )
    assert _find_create_workflow_call(run_output) is None


def test_find_update_workflow_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "update_workflow", {"workflow": "release-checklist", "name": "release-2026"}
            )
        ],
    )
    call = _find_update_workflow_call(run_output)
    assert call is not None
    assert call.workflow_ref == "release-checklist"
    assert call.name == "release-2026"
    assert call.description is None


def test_find_delete_workflow_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("delete_workflow", {"workflow": "release-checklist"})],
    )
    assert _find_delete_workflow_call(run_output) == "release-checklist"


def test_find_publish_workflow_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("publish_workflow", {"workflow": "release-checklist"})],
    )
    assert _find_publish_workflow_call(run_output) == "release-checklist"


def test_find_unpublish_workflow_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("unpublish_workflow", {"workflow": "release-checklist"})],
    )
    assert _find_unpublish_workflow_call(run_output) == "release-checklist"


def test_find_list_workflows_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("list_workflows", {})]
    )
    assert _find_list_workflows_call(run_output) is True
    assert _find_list_workflows_call(RunOutput(status=RunStatus.completed, tools=None)) is False


# --- end-to-end dispatch -------------------------------------------------


def _create_agent(
    client: TestClient, headers: dict[str, str], name: str, pattern: str = "go"
) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": f"Test agent {name}",
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
        "/api/v1/teams", json={"name": f"Workflow Tool Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"workflow-tool-test-{agent_id}"}, headers=headers
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


def _create_workflow_via_api(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/workflows", json={"name": name, "description": "test workflow"}, headers=headers
    )
    assert created.status_code == 201, created.text
    workflow_id: str = created.json()["id"]
    return workflow_id


def _give_entry_point(client: TestClient, headers: dict[str, str], workflow_id: str) -> None:
    """Adds a single human_input node and connects it as the entry point,
    the minimum a workflow needs to pass publish_workflow's "can this
    even run" check."""
    node = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "step", "node_type": "human_input"},
        headers=headers,
    )
    assert node.status_code == 201, node.text
    node_id: str = node.json()["id"]
    connection = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": None, "to_node_id": node_id},
        headers=headers,
    )
    assert connection.status_code == 201, connection.text


def test_create_workflow_creates_workflow(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Creator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_workflow")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("create_workflow", {"name": "new-workflow", "description": "fresh"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a workflow"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "created workflow 'new-workflow'" in messages[2]["content"]

    listed = client.get("/api/v1/workflows", headers=auth_headers).json()
    assert any(w["name"] == "new-workflow" and w["description"] == "fresh" for w in listed)


def test_create_workflow_rejects_invalid_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "PickyCreator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_workflow")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_workflow", {"name": "Not Valid!"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a workflow"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "lowercase letters/digits/hyphens" in messages[2]["content"]


def test_create_workflow_rejects_duplicate_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "DupeCreator")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "create_workflow")
    existing_name = f"dupe-workflow-{agent_id}"
    _create_workflow_via_api(client, auth_headers, existing_name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("create_workflow", {"name": existing_name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go create a workflow"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "already exists" in messages[2]["content"]


def test_update_workflow_renames_by_name(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Renamer")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workflow")
    old_name = f"rename-target-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, old_name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("update_workflow", {"workflow": old_name, "name": "renamed-workflow"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go rename it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "updated workflow" in messages[2]["content"]

    updated = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).json()
    assert updated["name"] == "renamed-workflow"


def test_update_workflow_no_changes_specified_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Indecisive")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workflow")
    name = f"indecisive-workflow-{agent_id}"
    _create_workflow_via_api(client, auth_headers, name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("update_workflow", {"workflow": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "didn't specify any changes" in messages[2]["content"]


def test_update_workflow_unknown_reference_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ConfusedUpdater")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workflow")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "update_workflow", {"workflow": "no-such-workflow", "name": "irrelevant"}
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no workflow with that id or name" in messages[2]["content"]


def test_update_workflow_rename_of_published_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387 / #356: a published workflow's name is its `/{name}` trigger
    surface. The trigger has no live session to grant an owner exception."""
    agent_id = _create_agent(client, auth_headers, "PublishedRenamer")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workflow")
    old_name = f"published-rename-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, old_name)
    _give_entry_point(client, auth_headers, workflow_id)
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers)
    assert published.status_code == 200, published.text

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution("update_workflow", {"workflow": old_name, "name": "should-not-rename"})
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go rename it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "requires a live owner session" in messages[2]["content"]

    updated = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).json()
    assert updated["name"] == old_name


def test_update_workflow_description_of_published_still_allowed(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP PATCH still lets any grant edit a published workflow's
    description; the tool must match."""
    agent_id = _create_agent(client, auth_headers, "PublishedDescriber")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "update_workflow")
    name = f"published-describe-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, name)
    _give_entry_point(client, auth_headers, workflow_id)
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers)
    assert published.status_code == 200, published.text

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "update_workflow", {"workflow": name, "description": "still just a blurb"}
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go update it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "updated workflow" in messages[2]["content"]

    updated = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).json()
    assert updated["name"] == name
    assert updated["description"] == "still just a blurb"


def test_delete_workflow_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387: HTTP delete_workflow is OwnerGrant. The trigger has no live
    session, so it refuses outright -- even a draft."""
    agent_id = _create_agent(client, auth_headers, "Deleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_workflow")
    name = f"delete-target-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_workflow", {"workflow": workflow_id})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "requires a live owner session" in messages[2]["content"]
    assert client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).status_code == 200


async def test_delete_workflow_trigger_does_not_queue_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387: a refused delete must not tombstone the workflow. The HTTP
    delete_workflow route still tombstones (#287); this trigger just
    never reaches that write."""
    agent_id = _create_agent(client, auth_headers, "SyncDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_workflow")
    name = f"delete-sync-target-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_workflow", {"workflow": workflow_id})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    client.get(f"/api/v1/rivulets/{rivulet.json()['id']}/messages", headers=auth_headers)

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("workflow", workflow_id))
        assert pending is None or pending.deleted is not True


def test_delete_workflow_unknown_reference_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ConfusedDeleter")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "delete_workflow")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("delete_workflow", {"workflow": "no-such-workflow"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go delete it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no workflow with that id or name" in messages[2]["content"]


def test_publish_workflow_with_entry_point_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387: HTTP publish_workflow is OwnerGrant. A ready draft still
    cannot be flipped live from chat."""
    agent_id = _create_agent(client, auth_headers, "Publisher")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "publish_workflow")
    name = f"publish-target-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, name)
    _give_entry_point(client, auth_headers, workflow_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("publish_workflow", {"workflow": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go publish it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "requires a live owner session" in messages[2]["content"]

    published = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).json()
    assert published["published"] is False


def test_publish_workflow_without_entry_point_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ImpatientPublisher")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "publish_workflow")
    name = f"no-entry-{agent_id}"
    _create_workflow_via_api(client, auth_headers, name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("publish_workflow", {"workflow": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go publish it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no entry point yet" in messages[2]["content"]


def test_publish_workflow_already_published_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "DoublePublisher")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "publish_workflow")
    name = f"already-published-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, name)
    _give_entry_point(client, auth_headers, workflow_id)
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers)
    assert published.status_code == 200, published.text

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("publish_workflow", {"workflow": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go publish it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "already published" in messages[2]["content"]


def test_unpublish_workflow_is_refused(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#387: HTTP unpublish_workflow is OwnerGrant. Detaching `/{name}`
    from chat is the same live-surface rewrite as publish."""
    agent_id = _create_agent(client, auth_headers, "Unpublisher")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "unpublish_workflow")
    name = f"unpublish-target-{agent_id}"
    workflow_id = _create_workflow_via_api(client, auth_headers, name)
    _give_entry_point(client, auth_headers, workflow_id)
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=auth_headers)
    assert published.status_code == 200, published.text

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("unpublish_workflow", {"workflow": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go unpublish it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "requires a live owner session" in messages[2]["content"]

    unpublished = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth_headers).json()
    assert unpublished["published"] is True


def test_unpublish_workflow_not_published_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "ImpatientUnpublisher")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    authorize_agent_for_builtin_tool(client, auth_headers, agent_id, "unpublish_workflow")
    name = f"still-draft-{agent_id}"
    _create_workflow_via_api(client, auth_headers, name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("unpublish_workflow", {"workflow": name})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go unpublish it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "isn't published" in messages[2]["content"]


def test_list_workflows_reports_existing_workflows(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Lister")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    name = f"list-target-{agent_id}"
    _create_workflow_via_api(client, auth_headers, name)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_workflows", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go list workflows"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert name in messages[2]["content"]
    assert "draft" in messages[2]["content"]
