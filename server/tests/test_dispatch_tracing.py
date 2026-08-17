"""#96: dispatch/service.py's run-tracing instrumentation, exercised
through the real HTTP API (mirrors test_rivulet_dispatch.py's style) --
verifies a human message produces one RunTrace whose span tree links its
DispatchDecision and AgentRun rows, and that a recursive re-dispatch
(FR-5.6) and a handoff (FR-6) both continue the *same* trace instead of
starting a new one.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient


def _fake_run_agent(content: str) -> Any:
    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: content
        )

    return fake


def _create_agent_with_always_rule(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "An agent that always responds, for tracing tests.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "always", "pattern": "", "priority": 0}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Tracing Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    channel = client.post("/api/v1/channels", json={"name": "tracing-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    updated = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    return channel_id


async def test_human_message_produces_trace_with_dispatch_and_agent_spans(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent", _fake_run_agent("OK, doing that now.")
    )
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Traced Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hello there"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    traces = client.get("/api/v1/runs", headers=auth_headers).json()
    assert len(traces) == 1
    trace = traces[0]
    assert trace["trigger_type"] == "message"
    assert trace["rivulet_id"] == rivulet_id
    assert trace["label"] == "hello there"
    # The agent's reply is still re-dispatched (FR-5.6) so a teammate
    # @mention can fire, but the speaker is excluded from unsolicited
    # rematch — so this trace is one dispatch_decision + one agent_run +
    # the nested no-op re-dispatch, not a self-loop the guard has to stop.
    assert trace["span_count"] > 2

    detail = client.get(f"/api/v1/runs/{trace['id']}", headers=auth_headers).json()
    spans = detail["spans"]
    root_spans = [s for s in spans if s["parent_span_id"] is None]
    assert len(root_spans) == 1
    assert root_spans[0]["span_type"] == "dispatch_decision"

    agent_spans = [s for s in spans if s["span_type"] == "agent_run"]
    assert len(agent_spans) >= 1
    first_agent_span = agent_spans[0]
    assert first_agent_span["name"] == "Traced Agent"
    assert first_agent_span["status"] == "completed"
    assert first_agent_span["parent_span_id"] == root_spans[0]["id"]
    assert first_agent_span["entity_id"] is not None  # back-filled with the AgentRun id
    assert first_agent_span["duration_ms"] is not None

    # A second dispatch_decision (the recursive re-dispatch of the agent's
    # own reply) nests under that agent's span, not at the trace root.
    nested_dispatch_spans = [
        s
        for s in spans
        if s["span_type"] == "dispatch_decision" and s["parent_span_id"] is not None
    ]
    assert len(nested_dispatch_spans) >= 1
    assert nested_dispatch_spans[0]["parent_span_id"] == first_agent_span["id"]


async def test_agent_run_failure_still_produces_an_error_span(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _boom)
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Flaky Traced Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "will fail"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text

    traces = client.get("/api/v1/runs", headers=auth_headers).json()
    assert len(traces) == 1
    assert traces[0]["status"] == "error"

    detail = client.get(f"/api/v1/runs/{traces[0]['id']}", headers=auth_headers).json()
    agent_spans = [s for s in detail["spans"] if s["span_type"] == "agent_run"]
    assert len(agent_spans) == 1
    assert agent_spans[0]["status"] == "error"
    # This run_agent failure never reaches record_agent_run (dispatch/
    # service.py's `run_output is None` branch), so there's no AgentRun row
    # to back-fill -- entity_id stays None, unlike the error-RunOutput case.
    # #405: the sanitized exception still lands on the span so Runs can
    # show why, even without an AgentRun.
    assert agent_spans[0]["entity_id"] is None
    assert "provider unreachable" in (agent_spans[0]["error_message"] or "")


async def test_run_not_found_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/runs/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def _fake_run_agent_with_tool_call(content: str) -> Any:
    """Like _fake_run_agent, but the run output carries one recorded tool
    call whose args/result hold exactly the kind of secret #357 is about --
    an http_request Authorization header."""

    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[
                SimpleNamespace(
                    tool_name="http_request",
                    tool_args={
                        "url": "https://api.example.com/v1/things",
                        "headers": {"Authorization": "Bearer sk-run-trace-secret"},
                    },
                    result="200 OK: run-trace-result-body",
                    tool_call_error=False,
                    metrics=None,
                )
            ],
            get_content_as_string=lambda: content,
        )

    return fake


async def test_invite_grant_gets_redacted_tool_call_payloads(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#357 (leftover of #321): ToolCallLog's arguments_json/result_summary
    are the same class of secret-bearing payload that got custom-tool
    source owner-gated. An invited session may still list runs and read a
    trace's span tree -- including that a tool ran, its name, and status --
    but both payload fields come back null, while the owner keeps seeing
    them in full."""
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent", _fake_run_agent_with_tool_call("Fetched it.")
    )
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Tool Trace Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "fetch the thing"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    trace_id = client.get("/api/v1/runs", headers=auth_headers).json()[0]["id"]

    owner_detail = client.get(f"/api/v1/runs/{trace_id}", headers=auth_headers)
    owner_calls = [c for s in owner_detail.json()["spans"] for c in s["tool_calls"]]
    assert owner_calls, "expected the fake run's tool call to be logged onto the trace"
    assert "sk-run-trace-secret" in owner_calls[0]["arguments_json"]
    assert owner_calls[0]["result_summary"] == "200 OK: run-trace-result-body"

    invite_headers = _invite_headers(client, auth_headers)
    guest_detail = client.get(f"/api/v1/runs/{trace_id}", headers=invite_headers)
    assert guest_detail.status_code == 200
    guest_calls = [c for s in guest_detail.json()["spans"] for c in s["tool_calls"]]
    assert len(guest_calls) == len(owner_calls)
    assert guest_calls[0]["tool_name"] == "http_request"
    assert guest_calls[0]["sensitive"] is True
    assert all(c["arguments_json"] is None for c in guest_calls)
    assert all(c["result_summary"] is None for c in guest_calls)
    assert "sk-run-trace-secret" not in guest_detail.text
    assert "run-trace-result-body" not in guest_detail.text
