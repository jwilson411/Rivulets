"""Assistant-as-orchestrator: specialists speak on handoff / @mention,
and Assistant stays on every channel."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from agno.models.response import ToolExecution
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from rivulets.agentos.tool_scopes import DEFAULT_AGENT_SCOPES
from rivulets.db.models import Agent
from rivulets.dispatch.engine import AgentDispatchInfo, DispatchMethod, DispatchResult
from rivulets.dispatch.orchestration import apply_orchestrator_lock, format_team_roster


def test_lock_routes_human_turns_to_orchestrator_only() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder", "assistant"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=False,
        from_agent_id=None,
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["assistant"]
    assert result.method is DispatchMethod.DEFAULT


def test_lock_drops_unsolicited_agent_rematch() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=False,
        from_agent_id="assistant",
        orchestrator_id="assistant",
    )
    assert result.agent_ids == []


def test_lock_preserves_mentions() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder"], method=DispatchMethod.MENTION),
        team_engaged=False,
        from_agent_id=None,
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["coder"]


def test_lock_stays_on_after_engage() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["coder"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=True,
        from_agent_id=None,
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["assistant"]
    assert result.method is DispatchMethod.DEFAULT


def test_lock_is_noop_without_orchestrator() -> None:
    original = DispatchResult(agent_ids=["coder"], method=DispatchMethod.DETERMINISTIC)
    assert (
        apply_orchestrator_lock(
            original, team_engaged=False, from_agent_id=None, orchestrator_id=None
        )
        is original
    )


def test_format_team_roster_lists_specialists_not_assistant() -> None:
    assistant = SimpleNamespace(name="Assistant", description="orchestrator")
    coder = SimpleNamespace(name="Coder", description="Writes code.")
    unnamed = SimpleNamespace(name="Scout", description="")
    roster = format_team_roster(
        [
            (cast(Agent, assistant), AgentDispatchInfo(agent_id="a", name="Assistant")),
            (cast(Agent, coder), AgentDispatchInfo(agent_id="c", name="Coder")),
            (cast(Agent, unnamed), AgentDispatchInfo(agent_id="s", name="Scout")),
        ]
    )
    assert "Coder: Writes code." in roster
    assert "- Scout" in roster
    assert "Assistant" not in roster.split("\n", 1)[1]


def test_lock_returns_specialist_reply_to_orchestrator() -> None:
    result = apply_orchestrator_lock(
        DispatchResult(agent_ids=["writer"], method=DispatchMethod.DETERMINISTIC),
        team_engaged=True,
        from_agent_id="coder",
        orchestrator_id="assistant",
    )
    assert result.agent_ids == ["assistant"]
    assert result.method is DispatchMethod.DEFAULT


def _fake_run_agent(content: str) -> Any:
    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: content
        )

    return fake


def _create_keyword_agent(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": f"Test agent {name}",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    agent_id: str = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=headers,
    )
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str], name: str = "orch-test"
) -> str:
    team = client.post("/api/v1/teams", json={"name": f"{name} team"}, headers=headers)
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)
    channel = client.post("/api/v1/channels", json={"name": name}, headers=headers)
    channel_id = channel.json()["id"]
    client.patch(f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers)
    return channel_id


def test_locked_human_message_reaches_only_assistant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, message: str, **_kwargs: object) -> Any:
        seen.append(message)
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Which widget?",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    channel_id = _create_channel_with_team(client, auth_headers, [coder])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    agent_names = [m["sender_name"] for m in messages if m["sender_type"] == "agent"]
    assert agent_names == ["Assistant"]
    assert all(m["sender_name"] != "WidgetCoder" for m in messages)


def test_mention_bypasses_lock(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent("WidgetCoder here."))
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="mention-lock")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@WidgetCoder tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["sender_name"] == "WidgetCoder" for m in messages)


def test_human_engage_nudges_assistant_not_keyword_specialists(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_run_agent(_db: object, _agent_id: str, message: str, **_kwargs: object) -> Any:
        seen.append(message)
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Picking someone.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="engage-lock")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    before = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert all(m["sender_name"] != "WidgetCoder" for m in before)

    engaged = client.post(
        f"/api/v1/rivulets/{rivulet_id}/engage-team",
        json={"reason": "Enough context"},
        headers=auth_headers,
    )
    assert engaged.status_code == 201, engaged.text
    assert engaged.json()["content_type"] == "team_engaged"

    after = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["content_type"] == "team_engaged" for m in after)
    assert all(m["sender_name"] != "WidgetCoder" for m in after)
    assert any(m["sender_name"] == "Assistant" for m in after)
    assert any("pick the right teammate" in message for message in seen)


def test_assistant_engage_team_routes_to_the_matching_specialist(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stabilization plan Phase 1.3 (F3): engage_team used to post the
    marker and stop — "engaged the team" followed by silence. It must now
    run a specialist-only routing pass over the human's request and
    actually invoke the one best match, via the normal handoff pipeline.
    It still routes exactly one specialist, never the whole roster."""
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    writer = _create_keyword_agent(client, auth_headers, "WidgetWriter")
    invoked: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        invoked.append(agent_id)
        if agent_id == assistant_id:
            if invoked.count(assistant_id) == 1:
                return SimpleNamespace(
                    status=RunStatus.completed,
                    tools=[
                        ToolExecution(
                            tool_name="engage_team", tool_args={"reason": "Need the coder"}
                        )
                    ],
                    get_content_as_string=lambda: "Bringing the team in.",
                )
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=None,
                get_content_as_string=lambda: "Summarizing.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Widget details follow.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(
        client, auth_headers, [coder, writer], name="tool-engage"
    )

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["content_type"] == "team_engaged" for m in messages)
    # The engage routed through the handoff pipeline to the keyword match.
    assert any(m["content_type"] == "handoff" for m in messages)
    assert any(m["sender_name"] == "WidgetCoder" for m in messages)
    # Exactly one specialist — engage still never wakes the whole roster.
    assert invoked.count(coder) == 1
    assert invoked.count(writer) == 0


def test_assistant_engage_team_with_no_matching_specialist_surfaces_notice(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of Phase 1.3: no specialist matched must produce a
    visible "tell me who" notice, not silence after the marker."""
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    invoked: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        invoked.append(agent_id)
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[ToolExecution(tool_name="engage_team", tool_args={"reason": "Someone act"})],
                get_content_as_string=lambda: "Engaging.",
            )
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "hi"
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="engage-nomatch")

    # "gadget" misses WidgetCoder's "widget" keyword; no LLM fallback is
    # configured in tests, so the routing pass genuinely matches nobody.
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "help with the gadget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any(m["content_type"] == "team_engaged" for m in messages)
    assert any(
        m["content_type"] == "system_alert" and "no specialist matched" in m["content"]
        for m in messages
    )
    assert invoked.count(coder) == 0


def test_assistant_prose_handoff_claim_is_redriven_to_a_real_handoff(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stabilization plan Phase 2.3 (F1): the Assistant *says* "I'll bring
    in the coder" without calling the handoff tool. The claim must be
    detected and the Assistant re-driven once with an explicit "call the
    tool now" — here the re-drive complies and the specialist runs."""
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    assistant_prompts: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            assistant_prompts.append(message)
            if len(assistant_prompts) == 1:
                return SimpleNamespace(
                    status=RunStatus.completed,
                    tools=None,
                    get_content_as_string=lambda: "I'll engage the coder on this right away.",
                )
            if "no handoff tool call was recorded" in message:
                return SimpleNamespace(
                    status=RunStatus.completed,
                    tools=[
                        ToolExecution(
                            tool_name="handoff",
                            tool_args={
                                "target_agent_name": "WidgetCoder",
                                "context": "Build the widget",
                            },
                        )
                    ],
                    get_content_as_string=lambda: "Handing off now.",
                )
            return SimpleNamespace(
                status=RunStatus.completed, tools=None, get_content_as_string=lambda: "Noted."
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Widget built.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="redrive-ok")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "build me a widget tracker"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    # The re-drive happened and carried the explicit instruction.
    assert len(assistant_prompts) >= 2
    assert "no handoff tool call was recorded" in assistant_prompts[1]
    # And it produced a real handoff that reached the specialist.
    assert any(m["content_type"] == "handoff" for m in messages)
    assert any(m["sender_name"] == "WidgetCoder" for m in messages)


def test_assistant_prose_handoff_claim_surfaces_after_failed_redrive(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the re-driven turn still narrates a handoff without calling the
    tool, the thread must show a visible notice — and the re-drive must
    not loop."""
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    assistant_runs = 0

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        nonlocal assistant_runs
        if agent_id == assistant_id:
            assistant_runs += 1
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "I'm going to engage the team for this.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="redrive-fail")

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "build me a widget tracker"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert assistant_runs == 2  # original turn + exactly one re-drive
    assert any(
        m["content_type"] == "system_alert" and "no handoff was actually made" in m["content"]
        for m in messages
    )
    assert all(m["sender_name"] != "WidgetCoder" for m in messages)


def test_assistant_answers_a_channel_with_no_team(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent("Here."))
    channel = client.post("/api/v1/channels", json={"name": "no-team-orch"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hello"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["sender_name"] == "Assistant"


def test_history_is_passed_to_the_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_run_agent(_db: object, _agent_id: str, message: str, **_kwargs: object) -> Any:
        seen.append(message)
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "ok"
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel = client.post("/api/v1/channels", json={"name": "hist-prompt"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    first = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "remember the color blue"},
        headers=auth_headers,
    )
    rivulet_id = first.json()["id"]
    client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "what color was that?"},
        headers=auth_headers,
    )
    assert len(seen) >= 2
    assert "remember the color blue" in seen[-1]
    assert "[Conversation so far]" in seen[-1]
    assert "[Current message]" in seen[-1]
    assert "what color was that?" in seen[-1]
    assert "[Channel team]" in seen[-1]


def test_handoff_invokes_specialist_and_reply_returns_to_assistant(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")
    writer = _create_keyword_agent(client, auth_headers, "WidgetWriter")
    invoked: list[str] = []

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        invoked.append(agent_id)
        if agent_id == assistant_id and invoked.count(assistant_id) == 1:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    ToolExecution(
                        tool_name="handoff",
                        tool_args={
                            "target_agent_name": "WidgetCoder",
                            "context": "Explain the widget",
                        },
                    )
                ],
                get_content_as_string=lambda: "Handing to the coder.",
            )
        if agent_id == coder:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=None,
                get_content_as_string=lambda: "The widget is a code thing.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Noted.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(
        client, auth_headers, [coder, writer], name="handoff-route"
    )
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    names = [m["sender_name"] for m in messages if m["sender_type"] == "agent"]
    assert names[0] == "Assistant"
    assert "WidgetCoder" in names
    assert "WidgetWriter" not in names
    assert names[-1] == "Assistant"
    assert invoked.count(writer) == 0


def test_hire_teammate_then_handoff_invokes_new_agent(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "WidgetCoder")

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    ToolExecution(
                        tool_name="hire_teammate",
                        tool_args={
                            "name": "DBA",
                            "role": "Reviews schemas and writes SQL.",
                            "instructions": "You are a database administrator.",
                            "assignment": "Check the users table.",
                        },
                    ),
                    ToolExecution(
                        tool_name="handoff",
                        tool_args={
                            "target_agent_name": "DBA",
                            "context": "Check the users table.",
                        },
                    ),
                ],
                get_content_as_string=lambda: "Hiring a DBA.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Schema looks fine.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="hire-dba")
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "we need a database review"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]
    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert any("hired @DBA" in (m["content"] or "") for m in messages)
    assert any(m["content_type"] == "handoff" for m in messages)
    assert any(m["sender_name"] == "DBA" for m in messages)
    created = client.get("/api/v1/agents", headers=auth_headers).json()
    assert any(a["name"] == "DBA" for a in created)


def test_hire_teammate_starts_with_new_agent_tools_and_scopes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#468: a hire must land with the same default kit as New agent,
    not an empty tool list that can only talk."""
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=[
                    ToolExecution(
                        tool_name="hire_teammate",
                        tool_args={
                            "name": "DBA",
                            "role": "Reviews schemas and writes SQL.",
                            "instructions": "You are a database administrator.",
                            "assignment": "Check the users table.",
                        },
                    )
                ],
                get_content_as_string=lambda: "Hiring a DBA.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=None,
            get_content_as_string=lambda: "Schema looks fine.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [], name="hire-tools")
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "we need a database review"},
        headers=auth_headers,
    )
    roster = client.get("/api/v1/agents", headers=auth_headers).json()
    hired = next(agent for agent in roster if agent["name"] == "DBA")
    catalog = client.get("/api/v1/tools", headers=auth_headers).json()
    assigned = client.get(f"/api/v1/agents/{hired['id']}/tools", headers=auth_headers).json()
    scopes = client.get(f"/api/v1/agents/{hired['id']}/tool-scopes", headers=auth_headers).json()
    assert set(assigned["tool_ids"]) == {tool["id"] for tool in catalog}
    assert set(scopes["scopes"]) == set(DEFAULT_AGENT_SCOPES)


def test_non_orchestrator_hire_is_ignored(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = client.get("/api/v1/agents", headers=auth_headers).json()
    assistant_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    coder = _create_keyword_agent(client, auth_headers, "HireCoder")

    async def fake_run_agent(_db: object, agent_id: str, _message: str, **_kwargs: object) -> Any:
        if agent_id == assistant_id:
            return SimpleNamespace(
                status=RunStatus.completed,
                tools=None,
                get_content_as_string=lambda: "Noted.",
            )
        return SimpleNamespace(
            status=RunStatus.completed,
            tools=[
                ToolExecution(
                    tool_name="hire_teammate",
                    tool_args={
                        "name": "SneakyDBA",
                        "role": "Should not be created.",
                        "instructions": "Nope.",
                        "assignment": "Nope.",
                    },
                )
            ],
            get_content_as_string=lambda: "Trying to hire.",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    channel_id = _create_channel_with_team(client, auth_headers, [coder], name="sneaky-hire")
    client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@HireCoder hire someone"},
        headers=auth_headers,
    )
    created = client.get("/api/v1/agents", headers=auth_headers).json()
    assert all(a["name"] != "SneakyDBA" for a in created)
