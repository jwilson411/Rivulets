"""#93: agent-authored/managed workflow schedules -- the
schedule_workflow/list_schedules/cancel_schedule tools
(tools/builtin/schedules.py) and their detection + handling in
dispatch/service.py. Mirrors test_handoff.py's style: agentos.run_agent is
monkeypatched to hand back a RunOutput with the tool call already baked
in, since real tool-call detection by a live model isn't something a fake
API key can produce.
"""

from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.dispatch.service import (
    MAX_AGENT_SCHEDULES,
    _find_cancel_schedule_call,  # pyright: ignore[reportPrivateUsage]
    _find_list_schedules_call,  # pyright: ignore[reportPrivateUsage]
    _find_schedule_workflow_call,  # pyright: ignore[reportPrivateUsage]
)
from rivulets.tools.builtin.schedules import cancel_schedule, list_schedules, schedule_workflow


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


def test_schedule_workflow_tool_returns_confirmation_string() -> None:
    assert schedule_workflow.entrypoint is not None
    result = schedule_workflow.entrypoint(workflow_name="digest", cron_expression="0 9 * * *")
    assert "digest" in result


def test_list_schedules_tool_returns_confirmation_string() -> None:
    assert list_schedules.entrypoint is not None
    assert "schedul" in list_schedules.entrypoint().lower()


def test_cancel_schedule_tool_returns_confirmation_string() -> None:
    assert cancel_schedule.entrypoint is not None
    assert "abc123" in cancel_schedule.entrypoint(schedule="abc123")


def test_find_schedule_workflow_call_extracts_args() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[
            _tool_execution(
                "schedule_workflow",
                {
                    "workflow_name": "digest",
                    "cron_expression": "0 9 * * *",
                    "input_content": "go",
                    "name": "daily digest",
                },
            )
        ],
    )
    call = _find_schedule_workflow_call(run_output)
    assert call is not None
    assert call.workflow_name == "digest"
    assert call.cron_expression == "0 9 * * *"
    assert call.fire_at is None
    assert call.input_content == "go"
    assert call.name == "daily digest"


def test_find_schedule_workflow_call_missing_name_returns_none() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("schedule_workflow", {"cron_expression": "0 9 * * *"})],
    )
    assert _find_schedule_workflow_call(run_output) is None


def test_find_list_schedules_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed, tools=[_tool_execution("list_schedules", {})]
    )
    assert _find_list_schedules_call(run_output) is True
    assert _find_list_schedules_call(RunOutput(status=RunStatus.completed, tools=None)) is False


def test_find_cancel_schedule_call() -> None:
    run_output = RunOutput(
        status=RunStatus.completed,
        tools=[_tool_execution("cancel_schedule", {"schedule": "daily digest"})],
    )
    assert _find_cancel_schedule_call(run_output) == "daily digest"


def _create_agent(
    client: TestClient, headers: dict[str, str], name: str, pattern: str = "go"
) -> str:
    """A keyword rule (matched against "go", present in every triggering
    message below but never in the fake agent reply "ok") rather than
    "always" -- same "keyword, not always" precaution test_handoff.py's
    helper takes, so the agent's own reply (re-dispatched recursively,
    FR-5.6) can't re-trigger itself into an infinite tool-call loop."""
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
    # Both team and channel names are unique-constrained, so this is
    # suffixed with the agent id -- several tests in this module create
    # more than one channel (one per agent) and would otherwise collide.
    team = client.post(
        "/api/v1/teams", json={"name": f"Schedule Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"schedule-test-{agent_id}"}, headers=headers
    )
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def _publish_workflow(client: TestClient, headers: dict[str, str], name: str) -> str:
    """Publishing (#84) refuses without an entry connection, so this adds
    a single transform node wired as the entry point before publishing —
    same minimal shape test_scheduler.py's _make_published_workflow uses
    at the DB layer."""
    created = client.post(
        "/api/v1/workflows", json={"name": name, "description": "test workflow"}, headers=headers
    )
    assert created.status_code == 201, created.text
    workflow_id: str = created.json()["id"]

    node = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "echo", "node_type": "transform", "config": {"template": "echo: {input}"}},
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

    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return workflow_id


def _fake_run_agent(tool_call: ToolExecution, reply: str = "ok"):
    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[tool_call],
            get_content_as_string=lambda: reply,
        )

    return fake_run_agent


def test_schedule_workflow_creates_disabled_schedule_pending_approval(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Scheduler")
    workflow_id = _publish_workflow(client, auth_headers, "digest")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {
                    "workflow_name": "digest",
                    "cron_expression": "0 9 * * *",
                    "name": "daily digest",
                },
            )
        ),
    )

    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go please schedule the digest"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent", "system"]
    confirmation = messages[2]["content"]
    assert "digest" in confirmation
    assert "pending human approval" in confirmation

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert len(schedules) == 1
    assert schedules[0]["enabled"] is False
    assert schedules[0]["created_by"] == agent_id
    assert schedules[0]["name"] == "daily digest"
    assert schedules[0]["cron_expression"] == "0 9 * * *"
    assert schedules[0]["run_once"] is False


def test_schedule_workflow_one_off_fire_at(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Reminder")
    workflow_id = _publish_workflow(client, auth_headers, "reminder-flow")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "reminder-flow", "fire_at": "2030-01-01T09:00:00Z"},
            )
        ),
    )

    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go remind me"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "pending human approval" in messages[2]["content"]

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert schedules[0]["run_once"] is True
    assert schedules[0]["cron_expression"] is None
    assert schedules[0]["next_fire_at"] == "2030-01-01T09:00:00Z"


def test_schedule_workflow_rejects_both_cron_and_fire_at(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Confused")
    workflow_id = _publish_workflow(client, auth_headers, "either-or")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {
                    "workflow_name": "either-or",
                    "cron_expression": "0 9 * * *",
                    "fire_at": "2030-01-01T09:00:00Z",
                },
            )
        ),
    )

    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "exactly one" in messages[2]["content"]

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert schedules == []


def test_schedule_workflow_rejects_unknown_workflow(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Confused2")

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "no-such-workflow", "cron_expression": "0 9 * * *"},
            )
        ),
    )

    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no published workflow" in messages[2]["content"]


def test_schedule_workflow_enforces_per_agent_cap(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Prolific")
    _publish_workflow(client, auth_headers, "capped")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    for i in range(MAX_AGENT_SCHEDULES):
        monkeypatch.setattr(
            "rivulets.dispatch.service.run_agent",
            _fake_run_agent(
                _tool_execution(
                    "schedule_workflow",
                    {"workflow_name": "capped", "cron_expression": "0 9 * * *", "name": f"s{i}"},
                )
            ),
        )
        rivulet = client.post(
            f"/api/v1/channels/{channel_id}/rivulets",
            json={"content": f"go schedule {i}"},
            headers=auth_headers,
        )
        assert rivulet.status_code == 201

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "capped", "cron_expression": "0 9 * * *", "name": "one-too-many"},
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule one more"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "cancel one first" in messages[-1]["content"]


def test_list_schedules_scoped_to_creating_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_a = _create_agent(client, auth_headers, "AgentA")
    agent_b = _create_agent(client, auth_headers, "AgentB")
    _publish_workflow(client, auth_headers, "shared-flow")

    channel_a = _create_channel_with_team(client, auth_headers, agent_a)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "shared-flow", "cron_expression": "0 9 * * *", "name": "a-only"},
            )
        ),
    )
    client.post(
        f"/api/v1/channels/{channel_a}/rivulets",
        json={"content": "go schedule mine"},
        headers=auth_headers,
    )

    channel_b = _create_channel_with_team(client, auth_headers, agent_b)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_schedules", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_b}/rivulets",
        json={"content": "go what do I have scheduled"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert messages[2]["content"] == "@AgentB has no scheduled workflows."


def test_list_schedules_shows_own_schedules(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Lister")
    _publish_workflow(client, auth_headers, "listed-flow")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {
                    "workflow_name": "listed-flow",
                    "cron_expression": "0 9 * * *",
                    "name": "morning check",
                },
            )
        ),
    )
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("list_schedules", {})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go what do I have scheduled"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    listing = messages[-1]["content"]
    assert "listed-flow" in listing
    assert "morning check" in listing
    assert "pending approval" in listing


def test_cancel_schedule_by_name_removes_own_schedule(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Canceller")
    workflow_id = _publish_workflow(client, auth_headers, "cancel-flow")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {
                    "workflow_name": "cancel-flow",
                    "cron_expression": "0 9 * * *",
                    "name": "to-cancel",
                },
            )
        ),
    )
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )
    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert len(schedules) == 1

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("cancel_schedule", {"schedule": "to-cancel"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go cancel it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "cancelled schedule" in messages[-1]["content"]

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert schedules == []


def test_cancel_schedule_cannot_remove_another_agents_schedule(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_a = _create_agent(client, auth_headers, "Owner")
    agent_b = _create_agent(client, auth_headers, "Interloper")
    workflow_id = _publish_workflow(client, auth_headers, "protected-flow")

    channel_a = _create_channel_with_team(client, auth_headers, agent_a)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {
                    "workflow_name": "protected-flow",
                    "cron_expression": "0 9 * * *",
                    "name": "owners-only",
                },
            )
        ),
    )
    client.post(
        f"/api/v1/channels/{channel_a}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )

    channel_b = _create_channel_with_team(client, auth_headers, agent_b)
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("cancel_schedule", {"schedule": "owners-only"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_b}/rivulets",
        json={"content": "go cancel it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "no schedule with that id or name was found" in messages[-1]["content"]

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert len(schedules) == 1


async def test_spent_one_off_schedules_do_not_count_against_the_cap(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-off reminder that already fired is inert (scheduler.py's
    _fire disables it and never re-arms it) -- it shouldn't permanently
    consume a slot of MAX_AGENT_SCHEDULES the way a still-active schedule
    does. Uses the real scheduler tick (like test_scheduler.py) rather
    than inserting WorkflowSchedule rows through a separate db_session --
    that fixture opens its own independent in-memory engine
    (conftest.py's db_session vs. client each call override_engine with
    their own make_engine(in_memory=True)), so mixing the two fixtures in
    one test silently splits state across two unrelated databases."""
    from rivulets.workflows.scheduler import _tick  # pyright: ignore[reportPrivateUsage]

    agent_id = _create_agent(client, auth_headers, "Reminders")
    workflow_id = _publish_workflow(client, auth_headers, "many-reminders")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    for i in range(MAX_AGENT_SCHEDULES):
        monkeypatch.setattr(
            "rivulets.dispatch.service.run_agent",
            _fake_run_agent(
                _tool_execution(
                    "schedule_workflow",
                    {
                        "workflow_name": "many-reminders",
                        "fire_at": "2020-01-01T00:00:00Z",
                        "name": f"spent-{i}",
                    },
                )
            ),
        )
        client.post(
            f"/api/v1/channels/{channel_id}/rivulets",
            json={"content": f"go remind me {i}"},
            headers=auth_headers,
        )

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert len(schedules) == MAX_AGENT_SCHEDULES
    for s in schedules:
        resp = client.patch(
            f"/api/v1/workflows/{workflow_id}/schedules/{s['id']}",
            json={"enabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

    await _tick()  # fires every due one-off, disabling each (scheduler.py's _fire)

    fired = client.get(f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers).json()
    assert all(not s["enabled"] and s["last_fired_at"] for s in fired)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "many-reminders", "cron_expression": "0 9 * * *", "name": "new"},
            )
        ),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule another"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "pending human approval" in messages[-1]["content"]
    assert "cancel one first" not in messages[-1]["content"]


async def test_reenabling_a_fired_one_off_schedule_is_rejected(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.workflows.scheduler import _tick  # pyright: ignore[reportPrivateUsage]

    agent_id = _create_agent(client, auth_headers, "OneOff")
    workflow_id = _publish_workflow(client, auth_headers, "one-off-flow")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "one-off-flow", "fire_at": "2020-01-01T00:00:00Z"},
            )
        ),
    )
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go remind me"},
        headers=auth_headers,
    )
    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    schedule_id = schedules[0]["id"]

    approved = client.patch(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule_id}",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert approved.status_code == 200, approved.text

    await _tick()

    fired = client.get(f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers).json()
    assert fired[0]["enabled"] is False
    assert fired[0]["last_fired_at"] is not None

    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule_id}",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "already fired" in resp.text

    unchanged = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()[0]
    assert unchanged["enabled"] is False
    assert unchanged["next_fire_at"] == "2020-01-01T00:00:00Z"


def test_cancel_schedule_with_ambiguous_name_asks_to_cancel_by_id(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Duplicator")
    workflow_id = _publish_workflow(client, auth_headers, "dup-flow")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)

    for _ in range(2):
        monkeypatch.setattr(
            "rivulets.dispatch.service.run_agent",
            _fake_run_agent(
                _tool_execution(
                    "schedule_workflow",
                    {
                        "workflow_name": "dup-flow",
                        "cron_expression": "0 9 * * *",
                        "name": "duplicate-name",
                    },
                )
            ),
        )
        client.post(
            f"/api/v1/channels/{channel_id}/rivulets",
            json={"content": "go schedule it"},
            headers=auth_headers,
        )

    schedules_before = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert len(schedules_before) == 2

    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent(_tool_execution("cancel_schedule", {"schedule": "duplicate-name"})),
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go cancel it"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert "matches 2 schedules" in messages[-1]["content"]
    assert "cancel by id instead" in messages[-1]["content"]

    schedules_after = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert len(schedules_after) == 2
