"""R-4 dispatcher hit-rate tracking (api/dispatch.py, #31) — aggregation
across DispatchDecision rows populated by dispatch/service.py's
`_dispatch_and_respond`. Mirrors test_usage_api.py's pattern of
monkeypatching the LLM-facing seam so no real provider call happens.

Every agent reply also gets re-dispatched (FR-5.6, dispatch/service.py's
recursion) — a single triggering message that reaches a real agent always
produces at least 2 DispatchDecision rows: one for the human message, one
for the agent's own "OK." reply being re-routed and matching nobody.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from rivulets.db.models import DispatchDecision
from rivulets.db.session import session_scope
from rivulets.dispatch.engine import AgentDispatchInfo, LlmFallbackResult


async def _fake_run_agent(*_args: object, **_kwargs: object) -> Any:
    return SimpleNamespace(
        status=RunStatus.completed, tools=None, get_content_as_string=lambda: "OK."
    )


def _create_agent_with_keyword_rule(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "Responds to widget questions.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text
    return agent_id


def _create_channel_with_team(
    client: TestClient, headers: dict[str, str], agent_ids: list[str]
) -> str:
    team = client.post("/api/v1/teams", json={"name": "Hit Rate Test Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)

    channel = client.post("/api/v1/channels", json={"name": "hit-rate-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    updated = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    return channel_id


def _send(client: TestClient, headers: dict[str, str], channel_id: str, content: str) -> None:
    resp = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": content}, headers=headers
    )
    assert resp.status_code == 201, resp.text


def _fake_llm_fallback(*, match_when: str | None) -> Any:
    """Stands in for dispatch/service.py's `build_llm_fallback(db)` — a
    function of `db` returning the actual fallback callable — always
    `invoked=True` (as if a provider were configured), matching the first
    agent only when `match_when` is a substring of the message. `None`
    never matches — used to simulate an LLM call that ran and found nobody,
    as opposed to one that was never attempted at all (see
    `rivulets.dispatch.service.build_llm_fallback`'s own short-circuits for
    the latter, exercised by leaving this monkeypatch off entirely)."""

    def build(_db: object) -> Any:
        async def fallback(message: str, agents: list[AgentDispatchInfo]) -> LlmFallbackResult:
            matched = match_when is not None and match_when in message and bool(agents)
            ids = [agents[0].agent_id] if matched else []
            return LlmFallbackResult(agent_ids=ids, invoked=True)

        return fallback

    return build


def test_hit_rate_empty_workspace(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/dispatch/hit-rate", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_decisions"] == 0
    assert body["hit_count"] == 0
    assert body["fallback_count"] == 0
    assert body["hit_rate"] is None
    assert body["fallback_rate"] is None
    assert body["fallback_warning"] is False
    assert body["by_method"] == []


def test_hit_rate_deterministic_match_counts_as_hit(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No provider configured in this test workspace, so the real LLM
    # fallback short-circuits (invoked=False) for the recursive re-dispatch
    # of the agent's "OK." reply — both decisions are hits.
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent)
    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Widget Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _send(client, auth_headers, channel_id, "tell me about the widget")

    body = client.get("/api/v1/dispatch/hit-rate", headers=auth_headers).json()
    assert body["total_decisions"] == 2
    assert body["hit_count"] == 2
    assert body["fallback_count"] == 0
    assert body["hit_rate"] == 1.0
    assert body["fallback_rate"] == 0.0
    assert body["fallback_warning"] is False
    assert {(m["method"], m["count"]) for m in body["by_method"]} == {
        ("deterministic", 1),
        ("none", 1),
    }


def test_hit_rate_llm_fallback_match_counts_as_fallback(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent)
    monkeypatch.setattr(
        "rivulets.dispatch.service.build_llm_fallback", _fake_llm_fallback(match_when="unrelated")
    )
    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Widget Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _send(client, auth_headers, channel_id, "something unrelated entirely")

    body = client.get("/api/v1/dispatch/hit-rate", headers=auth_headers).json()
    # Human message matches via LLM fallback (a cost). The agent's "OK."
    # reply is re-dispatched with the speaker excluded and no teammate
    # left to ask about, so that decision is a free none — not a second
    # fallback call against an empty roster.
    assert body["total_decisions"] == 2
    assert body["hit_count"] == 1
    assert body["fallback_count"] == 1
    assert body["hit_rate"] == 0.5
    assert body["fallback_rate"] == 0.5
    assert body["fallback_warning"] is False
    assert {(m["method"], m["count"]) for m in body["by_method"]} == {("llm", 1), ("none", 1)}


def test_hit_rate_llm_fallback_no_match_still_counts_as_fallback(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback ran (and cost money) even though it picked nobody.
    The default teammate then takes the human message; that invoke is
    unmocked here so it fails before recursion, leaving exactly one
    decision. `method` is "default" (not "none") — `llm_invoked` is
    still what marks the fallback as a cost event."""
    monkeypatch.setattr(
        "rivulets.dispatch.service.build_llm_fallback", _fake_llm_fallback(match_when=None)
    )
    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Widget Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _send(client, auth_headers, channel_id, "something unrelated entirely")

    body = client.get("/api/v1/dispatch/hit-rate", headers=auth_headers).json()
    assert body["total_decisions"] == 1
    assert body["fallback_count"] == 1
    assert body["hit_count"] == 0
    assert body["by_method"] == [{"method": "default", "count": 1}]


async def test_hit_rate_range_excludes_older_decisions(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.dispatch.service.build_llm_fallback", _fake_llm_fallback(match_when=None)
    )
    agent_id = _create_agent_with_keyword_rule(client, auth_headers, "Widget Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])
    _send(client, auth_headers, channel_id, "something unrelated entirely")

    async with session_scope() as session:
        decisions = (await session.execute(select(DispatchDecision))).scalars().all()
        assert len(decisions) == 1  # no match -> no agent invoked -> no recursion
        await session.execute(update(DispatchDecision).values(created_at="2000-01-01T00:00:00Z"))
        await session.commit()

    for range_ in ("day", "week", "month"):
        body = client.get(f"/api/v1/dispatch/hit-rate?range={range_}", headers=auth_headers).json()
        assert body["total_decisions"] == 0, f"range={range_}"
