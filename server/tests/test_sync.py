"""P2P sync (FR-9). Three layers of coverage here:

  - Pure vector-clock comparison/merge logic (sync/apply.py) — no network,
    no DB.
  - record_local_change / apply_remote_change (the generic entity path)
    and apply_remote_tool_change (tool's bespoke source-code-aware path)
    against a real (in-memory) DB via the db_session fixture — no network.
  - One real end-to-end SyncEngine test: two actual libp2p hosts in their
    own trio threads, matching PNet PSK, manual connect (FR-9.3, no mDNS
    involved in the connect path itself — though engine.start() does
    still register a real mDNS/zeroconf service as a side effect, same as
    production), gossipsub message delivery. This is the same scenario
    validated against a throwaway script before sync/engine.py was wired
    into the app at all — kept here as a real regression test since this
    is the most novel, highest-risk code in the whole feature and
    deserves more than "trust me, I ran it once."

The HTTP layer (api/sync.py) is tested via the client fixture, where
SyncEngine.start()/.stop() are no-op'd (see conftest.py) — status/
connect/disconnect are exercised against a not-running engine, which is
exactly what FR-9.5 says should still work.
"""

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.config import get_settings
from agent_hive.db.models import Agent, Channel, MCPServer, SyncConflict, Team, Tool, ToolVersion
from agent_hive.sync.apply import (
    AGENT_SPEC,
    CHANNEL_SPEC,
    MCP_SERVER_SPEC,
    TEAM_SPEC,
    ClockComparison,
    apply_remote_change,
    apply_remote_tool_change,
    compare_vector_clocks,
    merge_vector_clocks,
    record_local_change,
)
from agent_hive.sync.engine import SyncEngine

_AGENT_FIELDS = {
    "description": "A test agent used only in sync tests.",
    "instructions": "Do the thing.",
    "model": "openai:gpt-4",
}


def test_compare_vector_clocks_equal() -> None:
    assert compare_vector_clocks({"a": 1}, {"a": 1}) is ClockComparison.EQUAL
    assert compare_vector_clocks({}, {}) is ClockComparison.EQUAL


def test_compare_vector_clocks_remote_newer() -> None:
    assert compare_vector_clocks({"a": 1}, {"a": 2}) is ClockComparison.REMOTE_NEWER
    assert compare_vector_clocks({}, {"a": 1}) is ClockComparison.REMOTE_NEWER


def test_compare_vector_clocks_local_newer() -> None:
    assert compare_vector_clocks({"a": 2}, {"a": 1}) is ClockComparison.LOCAL_NEWER
    assert compare_vector_clocks({"a": 1}, {}) is ClockComparison.LOCAL_NEWER


def test_compare_vector_clocks_concurrent() -> None:
    assert compare_vector_clocks({"a": 2, "b": 0}, {"a": 1, "b": 1}) is ClockComparison.CONCURRENT


def test_merge_vector_clocks() -> None:
    assert merge_vector_clocks({"a": 2, "b": 0}, {"a": 1, "b": 3}) == {"a": 2, "b": 3}


async def test_record_local_change_bumps_own_component(db_session: AsyncSession) -> None:
    vc = await record_local_change(db_session, "agent", "agent-1", "node-a")
    assert vc == {"node-a": 1}
    vc2 = await record_local_change(db_session, "agent", "agent-1", "node-a")
    assert vc2 == {"node-a": 2}


async def test_apply_remote_agent_change_creates_new_agent(db_session: AsyncSession) -> None:
    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remote Agent", **_AGENT_FIELDS},
    )
    assert result.applied is True
    assert result.conflict is False

    agent = await db_session.get(Agent, "agent-1")
    assert agent is not None
    assert agent.name == "Remote Agent"
    assert agent.vector_clock == 1


async def test_apply_remote_agent_change_ignores_stale(db_session: AsyncSession) -> None:
    await record_local_change(db_session, "agent", "agent-1", "node-a")  # local at {node-a: 1}

    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-a": 0},
        "node-b",
        {"name": "Stale", **_AGENT_FIELDS},
    )

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Agent, "agent-1") is None  # never created


async def test_apply_remote_agent_change_detects_conflict(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-1", name="Local", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")  # local at {node-a: 1}

    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remote", **_AGENT_FIELDS},
    )

    assert result.applied is False
    assert result.conflict is True

    agent = await db_session.get(Agent, "agent-1")
    assert agent is not None
    assert agent.name == "Local"  # untouched

    conflicts = list((await db_session.execute(select(SyncConflict))).scalars().all())
    assert len(conflicts) == 1
    assert conflicts[0].remote_node_id == "node-b"
    assert conflicts[0].entity_id == "agent-1"
    assert conflicts[0].resolved is False


async def test_apply_remote_channel_change_creates_channel(db_session: AsyncSession) -> None:
    result = await apply_remote_change(
        db_session,
        CHANNEL_SPEC,
        "chan-1",
        {"node-b": 1},
        "node-b",
        {"name": "general", "description": "General chat", "position": 0, "archived": False},
    )
    assert result.applied is True
    channel = await db_session.get(Channel, "chan-1")
    assert channel is not None
    assert channel.name == "general"


async def test_apply_remote_team_change_creates_team(db_session: AsyncSession) -> None:
    result = await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-1",
        {"node-b": 1},
        "node-b",
        {"name": "Eng", "description": None},
    )
    assert result.applied is True
    team = await db_session.get(Team, "team-1")
    assert team is not None
    assert team.name == "Eng"


async def test_apply_remote_mcp_server_change_does_not_sync_connection_status(
    db_session: AsyncSession,
) -> None:
    """MCPServer.connected/last_connected_at are per-node status, not
    synced fields (see apply.py's module docstring) -- a freshly-applied
    remote server row must come in disconnected regardless of what the
    sender's own connection state was."""
    result = await apply_remote_change(
        db_session,
        MCP_SERVER_SPEC,
        "mcp-1",
        {"node-b": 1},
        "node-b",
        {"name": "Filesystem tools", "url": "http://127.0.0.1:9999/mcp"},
    )
    assert result.applied is True
    server = await db_session.get(MCPServer, "mcp-1")
    assert server is not None
    assert server.name == "Filesystem tools"
    assert server.connected is False
    assert server.last_connected_at is None


async def test_apply_remote_tool_change_writes_source_code_to_disk(
    db_session: AsyncSession,
) -> None:
    # conftest.py points AGENT_HIVE_WORKSPACE_DIR at an isolated temp dir
    # for the whole test session, so get_settings().tools_dir is already
    # safe to write into here without redirecting it further per-test.
    expected_path = get_settings().tools_dir / "add_numbers.py"

    result = await apply_remote_tool_change(
        db_session,
        "tool-1",
        {"node-b": 1},
        "node-b",
        {
            "name": "add_numbers",
            "description": "Adds two numbers.",
            "source_code": "def add_numbers(a, b):\n    return a + b\n",
        },
    )
    assert result.applied is True

    tool = await db_session.get(Tool, "tool-1")
    assert tool is not None
    assert tool.tool_type == "custom"
    assert tool.source_path == str(expected_path)
    assert expected_path.read_text() == "def add_numbers(a, b):\n    return a + b\n"

    versions = list(
        (await db_session.execute(select(ToolVersion).where(ToolVersion.tool_id == "tool-1")))
        .scalars()
        .all()
    )
    assert len(versions) == 1
    assert versions[0].version == 1


async def test_apply_remote_tool_change_detects_conflict(db_session: AsyncSession) -> None:
    source_path = get_settings().tools_dir / "add_numbers.py"
    db_session.add(
        Tool(
            id="tool-1",
            name="add_numbers",
            description="Local version.",
            tool_type="custom",
            source_path=str(source_path),
        )
    )
    await db_session.commit()
    await record_local_change(db_session, "tool", "tool-1", "node-a")

    result = await apply_remote_tool_change(
        db_session,
        "tool-1",
        {"node-b": 1},
        "node-b",
        {"name": "add_numbers", "description": "Remote version.", "source_code": "..."},
    )
    assert result.applied is False
    assert result.conflict is True

    tool = await db_session.get(Tool, "tool-1")
    assert tool is not None
    assert tool.description == "Local version."  # untouched


async def _get_first_addr(engine: SyncEngine) -> str:
    host = engine._host  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert host is not None
    return str(host.get_addrs()[0])


async def test_two_engines_sync_agent_state_change(tmp_path: Path) -> None:
    # Same PSK (required to connect at all -- PNet gates the connection on
    # it), but deliberately *different* mDNS workspace fingerprints. This
    # test wants to exercise manual connect (FR-9.3) in isolation: giving
    # both engines the same fingerprint let real mDNS auto-connect
    # (engine.py's _on_peer_discovered) race the test's own explicit
    # connect() call, and the two concurrent connection attempts between
    # the same pair of hosts reliably broke gossipsub mesh formation
    # (discovered by this test actually failing after auto-connect was
    # added -- not a hypothetical).
    psk_hex = hashlib.sha256(b"agent-hive-test-workspace").digest().hex()

    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    received = asyncio.Event()
    received_call: dict[str, object] = {}

    async def on_change(
        entity_type: str,
        entity_id: str,
        vector_clock: dict[str, int],
        origin_node_id: str,
        payload: dict[str, object],
    ) -> None:
        received_call["args"] = (entity_type, entity_id, vector_clock, origin_node_id, payload)
        received.set()

    engine_b.set_state_change_handler(on_change)

    await engine_a.start("agent-hive-test-workspace-fingerprint-a", psk_hex)
    await engine_b.start("agent-hive-test-workspace-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        peer = await engine_b.connect(addr)
        assert peer.peer_id == engine_a.node_id

        await asyncio.sleep(2.0)  # let gossipsub GRAFT the mesh (heartbeat-driven)

        await engine_a.publish_state_change(
            "agent", "agent-xyz", {"name": "Synced Agent"}, {engine_a.node_id: 1}
        )

        await asyncio.wait_for(received.wait(), timeout=10)
    finally:
        await engine_a.stop()
        await engine_b.stop()

    entity_type, entity_id, vector_clock, origin_node_id, payload = received_call["args"]  # type: ignore[misc]
    assert entity_type == "agent"
    assert entity_id == "agent-xyz"
    assert vector_clock == {engine_a.node_id: 1}
    assert origin_node_id == engine_a.node_id
    assert payload == {"name": "Synced Agent"}


def test_sync_status_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/sync/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {"running": False, "node_id": None, "peers": [], "pending_changes": 0}


def test_sync_connect_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sync/connect", json={"address": "/ip4/127.0.0.1/tcp/1/p2p/x"}, headers=auth_headers
    )
    assert response.status_code == 409


def test_sync_disconnect_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sync/disconnect", json={"peer_id": "some-peer"}, headers=auth_headers
    )
    assert response.status_code == 409


def test_list_conflicts_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/sync/conflicts", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_resolve_conflict_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sync/conflicts/nonexistent/resolve", json={"keep": "local"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_resolve_conflict_invalid_keep(client: TestClient, auth_headers: dict[str, str]) -> None:
    # No real conflict to resolve, but validation should fire before the 404 lookup context matters.
    response = client.post(
        "/api/v1/sync/conflicts/nonexistent/resolve", json={"keep": "bogus"}, headers=auth_headers
    )
    assert response.status_code in (400, 404)


def test_agent_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """FR-9.5: creating an agent must succeed even though the (no-op'd,
    per conftest.py) sync engine never actually starts in tests — this is
    exactly the "offline" case _publish_agent_change guards against."""
    response = client.post(
        "/api/v1/agents",
        json={
            "name": "offline-agent",
            "description": "Created while sync is not running.",
            "instructions": "Be helpful.",
            "model": "openai:gpt-4",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


def test_channel_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/channels", json={"name": "offline-channel"}, headers=auth_headers
    )
    assert response.status_code == 201, response.text


def test_team_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/teams", json={"name": "Offline Team"}, headers=auth_headers)
    assert response.status_code == 201, response.text


def test_mcp_server_register_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_discover_tools(url: str, timeout_seconds: int = 10) -> list[object]:  # noqa: ARG001
        return []

    monkeypatch.setattr("agent_hive.api.mcp_servers.discover_tools", _fake_discover_tools)

    response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "offline-server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


def test_tool_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/tools",
        json={"name": "offline_tool", "description": "A tool created while sync is not running."},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
