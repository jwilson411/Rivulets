"""End-to-end coverage of FR-4.1 (dispatch) + FR-12.1 (agent run -> message)
through the real HTTP API, with agentos.run_agent monkeypatched so no real
LLM call happens. dispatch/service.py's own selection logic (deterministic
rules, @mention, graceful skip on failure) all run for real.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Agent, AgentPeerPreference
from rivulets.dispatch.service import _resolve_remote_peer  # pyright: ignore[reportPrivateUsage]


def _fake_run_agent(content: str) -> Any:
    """A stand-in for agentos.run_agent: an async function returning
    something that duck-types agno's RunOutput just enough for
    dispatch/service.py, which checks .status and calls
    .get_content_as_string() on a successful run."""

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
            "description": "An agent that always responds, for testing.",
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
    team = client.post("/api/v1/teams", json={"name": "Test Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": agent_ids}, headers=headers)

    channel = client.post("/api/v1/channels", json={"name": "dispatch-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    updated = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    return channel_id


def test_keyword_rule_agent_responds_to_new_rivulet(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reply text deliberately doesn't contain "widget" — an "always" rule
    # would make the agent's own reply re-trigger itself (that scenario is
    # covered separately, as a turn-limit guard test); a keyword rule that
    # the *reply* doesn't match keeps this test to a single round-trip.
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent", _fake_run_agent("OK, doing that now.")
    )
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Keyword Agent",
            "description": "Responds to widget questions.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["sender_name"] == "Keyword Agent"
    assert messages[1]["content"] == "OK, doing that now."


def test_agent_with_no_rules_does_not_respond(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent("should not appear"))
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Silent Agent",
            "description": "An agent with no routing rules configured yet.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "anything at all"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]


def test_mention_invokes_agent_regardless_of_rules(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent("You called?"))
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "MentionOnly",
            "description": "An agent that only responds when @mentioned.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "mention_only", "pattern": "", "priority": 0}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hey @MentionOnly can you help?"},
        headers=auth_headers,
    )
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["content"] == "You called?"


def test_agent_run_failure_is_skipped_gracefully(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-2.4: a failing agent run must not 500 the request or block the
    human message from being persisted."""

    def _boom(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _boom)
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Flaky Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "anything at all"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]


def test_run_status_error_posts_system_alert_not_raw_error_text(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: found via a real end-to-end run with a bad API key.
    agno doesn't raise on a provider HTTP error — it returns a normal
    RunOutput with status=ERROR and the raw error string as `content`.
    Without checking `.status`, that error text got persisted as if the
    agent had said it."""

    async def fake(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.error,
            content="Error code: 401 - invalid x-api-key",
            get_content_as_string=lambda: "Error code: 401 - invalid x-api-key",
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake)
    agent_id = _create_agent_with_always_rule(client, auth_headers, "Broken Agent")
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "anything at all"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "system"]
    assert messages[1]["content_type"] == "system_alert"
    assert "401" not in messages[1]["content"]
    assert "Broken Agent" in messages[1]["content"]


def test_channel_with_no_team_gets_no_dispatch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel = client.post("/api/v1/channels", json={"name": "no-team"}, headers=auth_headers)
    channel_id = channel.json()["id"]

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hello"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]


def test_auto_mode_agent_receives_a_per_message_model_override(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto mode (#23): classify_tier picks a tier per message,
    resolve_tier_model maps it to a concrete model, and run_agent receives
    that as model_override -- a real per-message decision, not a model
    fixed once at agent creation. resolve_model itself runs for real here
    (the in-memory keyring fixture makes that safe, per
    test_providers_api.py's docstring) since it does no network I/O."""
    added_provider = client.post(
        "/api/v1/providers",
        json={"provider": "anthropic", "label": "Anthropic", "api_key": "sk-ant-test"},
        headers=auth_headers,
    )
    assert added_provider.status_code == 201, added_provider.text

    async def fake_classify_tier(*_args: object, **_kwargs: object) -> str:
        return "capable"

    monkeypatch.setattr("rivulets.dispatch.service.classify_tier", fake_classify_tier)

    received_overrides: list[object] = []

    async def fake_run_agent(*_args: object, **kwargs: object) -> Any:
        received_overrides.append(kwargs.get("model_override"))
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "Handled."
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "AutoAgent",
            "description": "Picks its own model per message.",
            "instructions": "Be helpful.",
            "model": "auto",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "mention_only", "pattern": "", "priority": 0}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "@AutoAgent solve this hard problem"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    assert len(received_overrides) == 1
    assert received_overrides[0] is not None

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert messages[1]["model_used"] == "anthropic:claude-opus-5"
    assert messages[1]["tier"] == "capable"


def test_non_auto_agent_never_invokes_the_classifier(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fake_classify_tier(*_args: object, **_kwargs: object) -> str:
        nonlocal called
        called = True
        return "capable"

    monkeypatch.setattr("rivulets.dispatch.service.classify_tier", fake_classify_tier)
    # Reply text deliberately doesn't contain "widget" -- see
    # test_keyword_rule_agent_responds_to_new_rivulet's comment on why
    # that keeps this a single round-trip instead of a recursive loop.
    monkeypatch.setattr(
        "rivulets.dispatch.service.run_agent", _fake_run_agent("OK, doing that now.")
    )

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Fixed Model Agent",
            "description": "Responds to widget questions.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=auth_headers,
    )
    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["model_used"] is None
    assert called is False


# ---------------------------------------------------------------------------
# Issue #10: multi-machine capability-aware task routing. _resolve_remote_peer
# is unit-tested directly (db_session fixture) against a fake, capability-
# aware engine -- same "swap in a test double for the network dependency"
# pattern test_sync.py's _FakeRunningEngine uses. load_capabilities is
# monkeypatched rather than written to the real (session-shared) sync_dir,
# so these tests don't depend on -- or leak into -- disk state any other
# test in the suite might touch.
# ---------------------------------------------------------------------------

_AGENT_FIELDS = {
    "description": "A test agent used only in remote-dispatch tests.",
    "instructions": "Do the thing.",
    "model": "anthropic:claude-3-5-haiku-latest",
}


def _fake_load_capabilities(tags: list[str]) -> Any:
    def load(_sync_dir: object) -> list[str]:
        return tags

    return load


class _FakeCapabilityEngine:
    node_id = "fake-local-node"

    def __init__(
        self, running: bool, peer_capabilities: dict[str, list[str]] | None = None
    ) -> None:
        self.running = running
        self._peer_capabilities = peer_capabilities or {}

    async def list_peer_capabilities(self) -> dict[str, list[str]]:
        return self._peer_capabilities


async def test_resolve_remote_peer_returns_none_without_preference(
    db_session: AsyncSession,
) -> None:
    agent = Agent(id="agent-1", name="A", **_AGENT_FIELDS)
    db_session.add(agent)
    await db_session.commit()

    assert await _resolve_remote_peer(db_session, agent) is None


async def test_resolve_remote_peer_returns_none_when_engine_not_running(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = Agent(id="agent-1", name="A", **_AGENT_FIELDS)
    db_session.add(agent)
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="gpu"))
    await db_session.commit()

    monkeypatch.setattr(
        "rivulets.dispatch.service.get_sync_engine",
        lambda: _FakeCapabilityEngine(running=False),
    )

    assert await _resolve_remote_peer(db_session, agent) is None


async def test_resolve_remote_peer_returns_none_when_this_node_already_matches(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = Agent(id="agent-1", name="A", **_AGENT_FIELDS)
    db_session.add(agent)
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="gpu"))
    await db_session.commit()

    monkeypatch.setattr(
        "rivulets.dispatch.service.load_capabilities", _fake_load_capabilities(["gpu"])
    )
    monkeypatch.setattr(
        "rivulets.dispatch.service.get_sync_engine",
        lambda: _FakeCapabilityEngine(running=True, peer_capabilities={"peer-x": ["gpu"]}),
    )

    # This node already has the preferred capability -- no network hop.
    assert await _resolve_remote_peer(db_session, agent) is None


async def test_resolve_remote_peer_returns_matching_peer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = Agent(id="agent-1", name="A", **_AGENT_FIELDS)
    db_session.add(agent)
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="gpu"))
    await db_session.commit()

    monkeypatch.setattr("rivulets.dispatch.service.load_capabilities", _fake_load_capabilities([]))
    monkeypatch.setattr(
        "rivulets.dispatch.service.get_sync_engine",
        lambda: _FakeCapabilityEngine(running=True, peer_capabilities={"peer-x": ["gpu"]}),
    )

    assert await _resolve_remote_peer(db_session, agent) == "peer-x"


async def test_resolve_remote_peer_returns_none_when_no_peer_matches(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = Agent(id="agent-1", name="A", **_AGENT_FIELDS)
    db_session.add(agent)
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="gpu"))
    await db_session.commit()

    monkeypatch.setattr("rivulets.dispatch.service.load_capabilities", _fake_load_capabilities([]))
    monkeypatch.setattr(
        "rivulets.dispatch.service.get_sync_engine",
        lambda: _FakeCapabilityEngine(running=True, peer_capabilities={"peer-x": ["cpu-heavy"]}),
    )

    assert await _resolve_remote_peer(db_session, agent) is None


def test_agent_with_remote_peer_preference_dispatches_via_rpc_not_locally(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full-pipeline integration: an agent pinned to a capability tag a
    connected peer advertises gets dispatched over the RPC instead of
    running locally -- run_agent must never fire, and no agent reply shows
    up in this node's own message list (it would arrive later via normal
    Message sync in production, which this test doesn't exercise)."""
    local_run_called = False

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        nonlocal local_run_called
        local_run_called = True
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "should not run"
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", fake_run_agent)
    monkeypatch.setattr("rivulets.dispatch.service.load_capabilities", _fake_load_capabilities([]))

    dispatch_calls: list[tuple[str, object]] = []

    class _FakeEngine:
        running = True

        async def list_peer_capabilities(self) -> dict[str, list[str]]:
            return {"peer-gpu": ["gpu"]}

        async def dispatch_agent_remotely(self, peer_id: str, request: object) -> bool:
            dispatch_calls.append((peer_id, request))
            return True

    monkeypatch.setattr("rivulets.dispatch.service.get_sync_engine", lambda: _FakeEngine())

    agent_id = _create_agent_with_always_rule(client, auth_headers, "GPU Agent")
    pref = client.put(
        f"/api/v1/agents/{agent_id}/peer-preference",
        json={"capability_tag": "gpu"},
        headers=auth_headers,
    )
    assert pref.status_code == 200, pref.text

    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "do the gpu thing"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    assert local_run_called is False
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0][0] == "peer-gpu"

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human"]


def test_agent_with_remote_peer_preference_falls_back_to_local_when_no_peer_matches(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same integration: a preference with no
    matching connected peer must still run locally, same as before issue
    #10 -- the offline/no-match fallback is "run locally", not "drop".
    Uses a keyword rule (not "always") whose reply text deliberately
    doesn't repeat the keyword, same as test_keyword_rule_agent_responds_
    to_new_rivulet -- an "always" rule here would make the agent's own
    successful reply re-trigger itself and recurse into a guard pause,
    unrelated to what this test is checking."""
    monkeypatch.setattr("rivulets.dispatch.service.run_agent", _fake_run_agent("ran locally"))
    monkeypatch.setattr("rivulets.dispatch.service.load_capabilities", _fake_load_capabilities([]))
    monkeypatch.setattr(
        "rivulets.dispatch.service.get_sync_engine",
        lambda: _FakeCapabilityEngine(running=True, peer_capabilities={}),
    )

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Unmatched GPU Agent",
            "description": "Responds to widget questions.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["widget"]', "priority": 10}]},
        headers=auth_headers,
    )
    pref = client.put(
        f"/api/v1/agents/{agent_id}/peer-preference",
        json={"capability_tag": "gpu"},
        headers=auth_headers,
    )
    assert pref.status_code == 200, pref.text

    channel_id = _create_channel_with_team(client, auth_headers, [agent_id])

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "tell me about the widget"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers).json()
    assert [m["sender_type"] for m in messages] == ["human", "agent"]
    assert messages[1]["content"] == "ran locally"
