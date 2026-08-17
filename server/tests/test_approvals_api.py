"""Unified approval queue (api/approvals.py, dispatch/approvals.py, #102)
through the real HTTP API. Exercises all three sources end to end -- an
agent-created schedule (#93), a tripped hard_stop budget cap (#97), and
the pattern is mirrored from test_schedule_tools.py/test_budget_
enforcement.py, which already cover each source's own pre-#102 behavior
in isolation; this file only adds the unified inbox/approve/reject layer
on top.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.models.response import ToolExecution
from agno.run.base import RunStatus
from fastapi.testclient import TestClient


def _tool_execution(tool_name: str, tool_args: dict[str, Any]) -> ToolExecution:
    return ToolExecution(tool_name=tool_name, tool_args=tool_args)


def _fake_run_agent_with_tool_call(tool_call: ToolExecution, reply: str = "ok"):
    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=[tool_call], get_content_as_string=lambda: reply
        )

    return fake_run_agent


def _create_agent(client: TestClient, headers: dict[str, str], name: str, pattern: str) -> str:
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
        "/api/v1/teams", json={"name": f"Approval Test Team {agent_id}"}, headers=headers
    )
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)
    channel = client.post(
        "/api/v1/channels", json={"name": f"approval-test-{agent_id}"}, headers=headers
    )
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def _publish_workflow(client: TestClient, headers: dict[str, str], name: str) -> str:
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
    node_id: str = node.json()["id"]
    client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": None, "to_node_id": node_id},
        headers=headers,
    )
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return workflow_id


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def test_list_is_empty_with_no_open_approvals(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    listed = client.get("/api/v1/approvals", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


def test_unknown_approval_id_404s(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/approvals/does-not-exist/approve", headers=auth_headers)
    assert response.status_code == 404


def test_approve_and_reject_require_owner_grant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Scheduler", "go")
    _publish_workflow(client, auth_headers, "digest")
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent_with_tool_call(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "digest", "cron_expression": "0 9 * * *", "name": "daily"},
            )
        ),
    )
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )
    approval_id = client.get("/api/v1/approvals", headers=auth_headers).json()[0]["id"]

    invite_headers = _invite_headers(client, auth_headers)
    # Reads stay open to any grant, matching budgets.py's own openness.
    listed = client.get("/api/v1/approvals", headers=invite_headers)
    assert listed.status_code == 200, listed.text

    forbidden = client.post(f"/api/v1/approvals/{approval_id}/approve", headers=invite_headers)
    assert forbidden.status_code == 403


def test_schedule_approval_approve_enables_the_schedule(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Scheduler", "go")
    workflow_id = _publish_workflow(client, auth_headers, "digest")
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent_with_tool_call(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "digest", "cron_expression": "0 9 * * *", "name": "daily"},
            )
        ),
    )
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )

    approvals = client.get("/api/v1/approvals", headers=auth_headers).json()
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["source_type"] == "schedule"
    assert approval["status"] == "pending"
    assert "Scheduler" in approval["title"]

    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=auth_headers)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["resolved_by"] is not None

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert schedules[0]["enabled"] is True

    # Terminal -- can't be approved a second time.
    conflict = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=auth_headers)
    assert conflict.status_code == 409


def test_schedule_approval_reject_leaves_schedule_disabled(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = _create_agent(client, auth_headers, "Scheduler", "go")
    workflow_id = _publish_workflow(client, auth_headers, "digest")
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent",
        _fake_run_agent_with_tool_call(
            _tool_execution(
                "schedule_workflow",
                {"workflow_name": "digest", "cron_expression": "0 9 * * *", "name": "daily"},
            )
        ),
    )
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "go schedule it"},
        headers=auth_headers,
    )
    approval = client.get("/api/v1/approvals", headers=auth_headers).json()[0]

    rejected = client.post(f"/api/v1/approvals/{approval['id']}/reject", headers=auth_headers)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    schedules = client.get(
        f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers
    ).json()
    assert schedules[0]["enabled"] is False


def _counting_fake_run_agent(calls: list[str]) -> Any:
    async def fake(_db: object, agent_id: str, *_args: object, **_kwargs: object) -> Any:
        calls.append(agent_id)
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "OK.",
            metrics=SimpleNamespace(input_tokens=0, output_tokens=250_000, total_tokens=250_000),
        )

    return fake


def test_budget_approval_approve_unblocks_current_period(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _counting_fake_run_agent(calls))

    agent_id = _create_agent(client, auth_headers, "Spendy", "widget")
    channel_id = _create_channel_with_team(client, auth_headers, agent_id)
    cap = client.post(
        "/api/v1/budgets",
        json={
            "scope_type": "agent",
            "agent_id": agent_id,
            "period": "day",
            "limit_usd": 0.5,
            "action": "hard_stop",
        },
        headers=auth_headers,
    )
    assert cap.status_code == 201, cap.text

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    assert len(calls) == 1  # in-flight run always finishes (#97)

    blocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another widget question"},
        headers=auth_headers,
    )
    assert blocked.status_code == 201, blocked.text
    assert len(calls) == 1  # refused, no model call

    approvals = client.get("/api/v1/approvals", headers=auth_headers).json()
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["source_type"] == "budget"
    assert approval["budget_cap_id"] == cap.json()["id"]

    # Triggering the block again must not grow a second row (dedup, same
    # reasoning as BudgetCapState.alerted_at's own per-period dedup).
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "yet another widget question"},
        headers=auth_headers,
    )
    assert len(client.get("/api/v1/approvals", headers=auth_headers).json()) == 1

    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=auth_headers)
    assert approved.status_code == 200, approved.text

    unblocked = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "one more widget question"},
        headers=auth_headers,
    )
    assert unblocked.status_code == 201, unblocked.text
    assert len(calls) == 2  # override took effect, the model ran again

    status_check = client.get(f"/api/v1/budgets/{cap.json()['id']}/status", headers=auth_headers)
    assert status_check.json()["override_active"] is True
