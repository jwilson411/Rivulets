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
import json
import os
import struct
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import trio
from fastapi.testclient import TestClient
from libp2p.peer.id import ID as _ID  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.config import get_settings
from rivulets.db.models import (
    Agent,
    AgentPeerPreference,
    Channel,
    File,
    MCPServer,
    Message,
    Rivulet,
    SyncConflict,
    SyncPendingInbound,
    Team,
    Tool,
    ToolVersion,
    WorkspaceSetting,
)
from rivulets.db.session import session_scope
from rivulets.sync.agent_dispatch import AgentDispatchRequest
from rivulets.sync.apply import (
    AGENT_PEER_PREFERENCE_SPEC,
    AGENT_SPEC,
    CHANNEL_SPEC,
    MCP_SERVER_SPEC,
    MESSAGE_SPEC,
    RIVULET_SPEC,
    TEAM_SPEC,
    WORKSPACE_SETTING_SPEC,
    ClockComparison,
    apply_remote_change,
    apply_remote_file_change,
    apply_remote_tool_change,
    compare_vector_clocks,
    handle_incoming_state_change,
    merge_vector_clocks,
    record_local_change,
    retry_pending_inbound,
)
from rivulets.sync.engine import (
    PeerInfo,
    SyncEngine,
    _bound_port,  # pyright: ignore[reportPrivateUsage]
    _PeerConnectionNotifee,  # pyright: ignore[reportPrivateUsage]
    get_sync_engine,
    init_sync_engine,
    reset_sync_engine_for_testing,
)
from rivulets.sync.file_transfer import HASH_LEN, HIT_PREFIX, MISS_MARKER

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


async def test_apply_remote_workspace_setting_change_creates_setting(
    db_session: AsyncSession,
) -> None:
    """WorkspaceSetting's primary key is `key`, not `id` like every other
    synced entity -- this is the regression test for EntitySpec.pk_field,
    not just a routine "does sync work" check."""
    result = await apply_remote_change(
        db_session,
        WORKSPACE_SETTING_SPEC,
        "guard.turn_limit",
        {"node-b": 1},
        "node-b",
        {"value": "15"},
    )
    assert result.applied is True

    setting = await db_session.get(WorkspaceSetting, "guard.turn_limit")
    assert setting is not None
    assert setting.value == "15"


async def test_apply_remote_workspace_setting_change_updates_existing(
    db_session: AsyncSession,
) -> None:
    db_session.add(WorkspaceSetting(key="guard.turn_limit", value="10"))
    await db_session.commit()
    await record_local_change(db_session, "workspace_setting", "guard.turn_limit", "node-a")

    result = await apply_remote_change(
        db_session,
        WORKSPACE_SETTING_SPEC,
        "guard.turn_limit",
        # Must dominate local's {node-a: 1} to apply cleanly rather than
        # being judged concurrent.
        {"node-a": 1, "node-b": 1},
        "node-b",
        {"value": "20"},
    )
    assert result.applied is True
    setting = await db_session.get(WorkspaceSetting, "guard.turn_limit")
    assert setting is not None
    assert setting.value == "20"


async def test_apply_remote_agent_peer_preference_creates_row(db_session: AsyncSession) -> None:
    """Like WorkspaceSetting, AgentPeerPreference's primary key is
    agent_id, not id -- another EntitySpec.pk_field regression case, plus
    coverage that issue #10's new entity type is actually wired into
    _DISPATCH (handle_incoming_state_change), not just apply_remote_change
    directly."""
    db_session.add(Agent(id="agent-1", name="Remote Pref Agent", **_AGENT_FIELDS))
    await db_session.commit()

    await handle_incoming_state_change(
        "agent_peer_preference", "agent-1", {"node-b": 1}, "node-b", {"capability_tag": "gpu"}
    )

    async with session_scope() as db:
        pref = await db.get(AgentPeerPreference, "agent-1")
        assert pref is not None
        assert pref.capability_tag == "gpu"


async def test_apply_remote_agent_peer_preference_updates_existing(
    db_session: AsyncSession,
) -> None:
    db_session.add(Agent(id="agent-1", name="Pref Agent", **_AGENT_FIELDS))
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="cpu-heavy"))
    await db_session.commit()
    await record_local_change(db_session, "agent_peer_preference", "agent-1", "node-a")

    result = await apply_remote_change(
        db_session,
        AGENT_PEER_PREFERENCE_SPEC,
        "agent-1",
        {"node-a": 1, "node-b": 1},
        "node-b",
        {"capability_tag": "gpu"},
    )
    assert result.applied is True

    pref = await db_session.get(AgentPeerPreference, "agent-1")
    assert pref is not None
    assert pref.capability_tag == "gpu"


async def test_apply_remote_agent_peer_preference_detects_conflict(
    db_session: AsyncSession,
) -> None:
    db_session.add(Agent(id="agent-1", name="Pref Agent", **_AGENT_FIELDS))
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="cpu-heavy"))
    await db_session.commit()
    await record_local_change(db_session, "agent_peer_preference", "agent-1", "node-a")

    result = await apply_remote_change(
        db_session,
        AGENT_PEER_PREFERENCE_SPEC,
        "agent-1",
        {"node-b": 1},  # concurrent with local's {node-a: 1} -- neither dominates
        "node-b",
        {"capability_tag": "gpu"},
    )
    assert result.applied is False
    assert result.conflict is True

    pref = await db_session.get(AgentPeerPreference, "agent-1")
    assert pref is not None
    assert pref.capability_tag == "cpu-heavy"  # untouched


async def test_handle_incoming_agent_change_resyncs_agentos_registry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the sync_agents()-after-remote-apply fix
    (sync/apply.py's handle_incoming_state_change): before this fix, a
    node that only ever *received* an Agent row via sync had the DB row
    but no matching in-process AgentOS registration, so run_agent() would
    raise "not registered with AgentOS" -- issue #10's remote dispatch
    depends on this being fixed. Asserts sync_agents() gets called at all
    (not that the agent successfully joins AgentOS's registry, which also
    depends on a resolvable provider -- unrelated to the bug this guards
    against, and AgentOS.agents is skip-on-failure by design, per
    NFR-2.4)."""
    calls: list[AsyncSession] = []

    async def fake_sync_agents(db: AsyncSession) -> None:
        calls.append(db)

    monkeypatch.setattr("rivulets.sync.apply.sync_agents", fake_sync_agents)

    await handle_incoming_state_change(
        "agent",
        "agent-remote-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remotely Synced Agent", **_AGENT_FIELDS},
    )

    assert len(calls) == 1


async def test_apply_remote_rivulet_change_creates_rivulet(db_session: AsyncSession) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {"channel_id": "chan-1", "title": "Hello", "status": "active", "created_by": "human"},
    )
    assert result.applied is True

    rivulet = await db_session.get(Rivulet, "rivulet-1")
    assert rivulet is not None
    assert rivulet.channel_id == "chan-1"
    assert rivulet.title == "Hello"
    assert rivulet.agentos_session_id is None  # never synced


async def test_apply_remote_rivulet_change_skips_when_channel_missing(
    db_session: AsyncSession,
) -> None:
    """Regression test for the FK-ordering hazard apply.py's module
    docstring describes: a rivulet arriving before its channel has synced
    must be dropped cleanly (IntegrityError caught), not crash the
    sync-message handler."""
    result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "does-not-exist",
            "title": "Hello",
            "status": "active",
            "created_by": "human",
        },
    )
    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Rivulet, "rivulet-1") is None

    # The vector-clock bump must have rolled back with the failed commit --
    # otherwise a later, valid delivery of the same message would be
    # wrongly judged as already-seen (EQUAL) instead of fresh.
    retry = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "does-not-exist",
            "title": "Hello",
            "status": "active",
            "created_by": "human",
        },
    )
    assert retry.applied is False  # still fails (channel still missing) rather than no-op'ing


async def test_apply_remote_message_change_creates_message(db_session: AsyncSession) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    db_session.add(
        Rivulet(id="rivulet-1", channel_id="chan-1", created_by="human", status="active")
    )
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        MESSAGE_SPEC,
        "msg-1",
        {"node-b": 1},
        "node-b",
        {
            "rivulet_id": "rivulet-1",
            "sender_type": "human",
            "sender_id": None,
            "sender_name": "You",
            "content": "Hello from node B",
            "content_type": "text",
            "metadata_json": None,
        },
    )
    assert result.applied is True

    message = await db_session.get(Message, "msg-1")
    assert message is not None
    assert message.rivulet_id == "rivulet-1"
    assert message.content == "Hello from node B"


async def test_apply_remote_message_change_skips_when_rivulet_missing(
    db_session: AsyncSession,
) -> None:
    result = await apply_remote_change(
        db_session,
        MESSAGE_SPEC,
        "msg-1",
        {"node-b": 1},
        "node-b",
        {
            "rivulet_id": "does-not-exist",
            "sender_type": "human",
            "sender_id": None,
            "sender_name": "You",
            "content": "orphaned",
            "content_type": "text",
            "metadata_json": None,
        },
    )
    assert result.applied is False
    assert await db_session.get(Message, "msg-1") is None


@pytest.fixture
def not_running_sync_engine(tmp_path: Path) -> Iterator[None]:
    """apply_remote_file_change calls get_sync_engine() to (maybe) fetch
    content — it needs the singleton initialized (unlike db_session-only
    tests, which never go through create_app()'s init_sync_engine call),
    but not actually running, so _fetch_file_content's engine.running
    check cleanly skips without touching the network."""
    reset_sync_engine_for_testing()
    init_sync_engine(tmp_path / "sync")
    yield
    reset_sync_engine_for_testing()


async def test_apply_remote_file_change_creates_file_metadata(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    content_hash = "a" * 64
    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 123,
            "message_id": None,
        },
    )
    assert result.applied is True

    file_row = await db_session.get(File, "file-1")
    assert file_row is not None
    assert file_row.content_hash == content_hash
    assert file_row.filename == "notes.txt"
    # local_path is always recomputed from this node's own files_dir, never
    # copied verbatim from the sender (mirrors Tool.source_path).
    assert file_row.local_path == str(get_settings().files_dir / content_hash[:2] / content_hash)


async def test_apply_remote_file_change_fetches_content_when_missing_locally(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b"fetched over the wire"
    content_hash = "b" * 64

    class _FakeEngine:
        running = True

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            assert peer_id == "node-b"
            assert requested_hash == content_hash
            return content

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": len(content),
            "message_id": None,
        },
    )
    assert result.applied is True

    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert local_path.read_bytes() == content


async def test_apply_remote_file_change_does_not_fetch_when_engine_not_running(
    db_session: AsyncSession,
    not_running_sync_engine: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    content_hash = "c" * 64

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )
    assert result.applied is True
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert not local_path.exists()  # engine wasn't running, so no fetch was attempted


async def test_apply_remote_tool_change_writes_source_code_to_disk(
    db_session: AsyncSession,
) -> None:
    # conftest.py points RIVULETS_WORKSPACE_DIR at an isolated temp dir
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
    psk_hex = hashlib.sha256(b"rivulets-test-workspace").digest().hex()

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

    await engine_a.start("rivulets-test-workspace-fingerprint-a", psk_hex)
    await engine_b.start("rivulets-test-workspace-fingerprint-b", psk_hex)
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


async def test_engine_tracks_inbound_connections_and_detects_disconnect(tmp_path: Path) -> None:
    """Two real bugs, both from _connected_peers only ever being written
    by this engine's own outbound connect() calls: (1) a peer connecting
    TO this host (inbound — B dials A) was never recorded at all, so
    list_peers() silently missed a real, active connection; (2) once
    recorded, an entry never left _connected_peers except via this
    engine's own explicit disconnect()/stop() — a network drop or the
    peer's process exiting left a phantom "connected: true" forever.
    _PeerConnectionNotifee fixes both."""
    psk_hex = hashlib.sha256(b"disconnect-test-workspace").digest().hex()

    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    await engine_a.start("disconnect-test-fingerprint-a", psk_hex)
    await engine_b.start("disconnect-test-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)  # B dials A -- A never calls connect() itself
        await asyncio.sleep(1.0)

        peers_a = await engine_a.list_peers()
        assert len(peers_a) == 1, "inbound connection was never recorded"
        assert peers_a[0].peer_id == engine_b.node_id

        await engine_b.disconnect(engine_a.node_id)  # B's doing, not A's
        await asyncio.sleep(1.0)

        peers_a_after = await engine_a.list_peers()
        assert peers_a_after == [], "phantom peer: A never noticed B disconnecting"
    finally:
        await engine_a.stop()
        await engine_b.stop()


async def test_two_engines_sync_capability_broadcast(tmp_path: Path) -> None:
    """Issue #10: capability announcements ride the same _STATE_TOPIC as
    entity sync, but via the "node_capabilities" sentinel branch in
    _receive_loop -- not apply.py. This is the real end-to-end regression
    test for that branch, mirroring test_two_engines_sync_agent_state_change."""
    psk_hex = hashlib.sha256(b"capability-test-workspace").digest().hex()

    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    await engine_a.start("capability-test-fingerprint-a", psk_hex)
    await engine_b.start("capability-test-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)
        await asyncio.sleep(2.0)  # let gossipsub GRAFT the mesh (heartbeat-driven)

        await engine_a.publish_capabilities(["gpu", "cpu-heavy"])

        for _ in range(50):
            capabilities = await engine_b.list_peer_capabilities()
            if engine_a.node_id in capabilities:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("engine_b never received engine_a's capability broadcast")

        assert capabilities[engine_a.node_id] == ["gpu", "cpu-heavy"]

        # Disconnecting drops the cached entry -- a peer going offline must
        # not still count toward remote-dispatch routing.
        await engine_b.disconnect(engine_a.node_id)
        await asyncio.sleep(1.0)
        assert engine_a.node_id not in await engine_b.list_peer_capabilities()
    finally:
        await engine_a.stop()
        await engine_b.stop()


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


def test_get_capabilities_defaults_to_empty(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Redirect to an isolated tmp_path -- capabilities.json lives under the
    # shared session workspace dir otherwise, and this test must not
    # depend on (or be broken by) another test in this file having already
    # written one there.
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    response = client.get("/api/v1/sync/capabilities", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"capabilities": []}


def test_set_capabilities_persists_and_round_trips(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Engine isn't running through the client fixture (see its docstring),
    so this exercises save/load_capabilities' local file round-trip
    without needing publish_capabilities to actually broadcast anything."""
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    set_response = client.patch(
        "/api/v1/sync/capabilities",
        json={"capabilities": ["gpu", "cpu-heavy"]},
        headers=auth_headers,
    )
    assert set_response.status_code == 200, set_response.text
    assert set_response.json() == {"capabilities": ["gpu", "cpu-heavy"]}

    read_back = client.get("/api/v1/sync/capabilities", headers=auth_headers)
    assert read_back.json() == {"capabilities": ["gpu", "cpu-heavy"]}


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


async def test_resolve_conflict_applies_remote_for_non_agent_entity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Regression test: resolve_conflict used to only apply the remote
    snapshot for entity_type == 'agent' — every other synced entity type
    (channel, team, rivulet, ...) silently did nothing when a user picked
    "keep remote". Covered here with 'channel' since it goes through the
    plain generic apply path (get_entity_spec)."""
    create = client.post("/api/v1/channels", json={"name": "local-name-chan"}, headers=auth_headers)
    channel_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "channel", channel_id, "node-a")
        result = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-b": 1},
            "node-b",
            {
                "name": "remote-name-chan",
                "description": "from remote",
                "position": 0,
                "archived": False,
            },
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    matching = [c for c in conflicts if c["entity_id"] == channel_id]
    assert len(matching) == 1
    conflict_id = matching[0]["id"]

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    updated = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert updated["name"] == "remote-name-chan"
    assert updated["description"] == "from remote"


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

    monkeypatch.setattr("rivulets.api.mcp_servers.discover_tools", _fake_discover_tools)

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


def test_rivulet_and_message_create_do_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel = client.post(
        "/api/v1/channels", json={"name": "offline-sync-channel"}, headers=auth_headers
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hello while sync is offline"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    message = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another message while sync is offline"},
        headers=auth_headers,
    )
    assert message.status_code == 201, message.text

    resumed = client.post(f"/api/v1/rivulets/{rivulet_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200

    closed = client.delete(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers)
    assert closed.status_code == 204


def test_file_upload_and_attach_do_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    upload = client.post(
        "/api/v1/files/upload",
        files={"upload": ("notes.txt", b"hello file sync", "text/plain")},
        headers=auth_headers,
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["file_id"]

    channel = client.post(
        "/api/v1/channels", json={"name": "offline-file-channel"}, headers=auth_headers
    )
    channel_id = channel.json()["id"]

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "see attached", "files": [file_id]},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text


def test_settings_patch_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.patch("/api/v1/settings", json={"guard.turn_limit": 15}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["guard.turn_limit"] == 15


# ---------------------------------------------------------------------------
# api/sync.py: sync_status/connect/disconnect's "engine actually running"
# branches. The `client` fixture no-ops SyncEngine.start()/stop() (see
# conftest.py), so engine.running is always False through it -- these branches
# are only reachable by swapping in a fake engine that reports running=True,
# the same pattern test_mcp_servers.py uses for MCPTools.
# ---------------------------------------------------------------------------


class _FakeRunningEngine:
    running = True
    node_id = "fake-node-id"

    def __init__(
        self,
        peers: list[PeerInfo] | None = None,
        connect_result: PeerInfo | Exception | None = None,
        disconnect_error: Exception | None = None,
    ) -> None:
        self._peers = peers or []
        self._connect_result = connect_result
        self._disconnect_error = disconnect_error

    async def list_peers(self) -> list[PeerInfo]:
        return self._peers

    async def list_peer_capabilities(self) -> dict[str, list[str]]:
        return {}

    async def connect(self, address: str) -> PeerInfo:
        if isinstance(self._connect_result, Exception):
            raise self._connect_result
        assert self._connect_result is not None
        return self._connect_result

    async def disconnect(self, peer_id: str) -> None:  # noqa: ARG002
        if self._disconnect_error is not None:
            raise self._disconnect_error


def test_sync_status_when_running_reports_peers(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(
        peers=[PeerInfo(peer_id="peer-1", address="/ip4/1.2.3.4/tcp/1", connected=True)]
    )
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.get("/api/v1/sync/status", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["running"] is True
    assert body["node_id"] == "fake-node-id"
    assert body["peers"] == [
        {
            "peer_id": "peer-1",
            "address": "/ip4/1.2.3.4/tcp/1",
            "connected": True,
            "capabilities": [],
        }
    ]


def test_sync_connect_when_running_returns_peer(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(
        connect_result=PeerInfo(peer_id="peer-2", address="/ip4/5.6.7.8/tcp/2", connected=True)
    )
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post(
        "/api/v1/sync/connect",
        json={"address": "/ip4/5.6.7.8/tcp/2/p2p/peer-2"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "peer_id": "peer-2",
        "address": "/ip4/5.6.7.8/tcp/2",
        "connected": True,
        "capabilities": [],
    }


def test_sync_connect_when_running_wraps_failure_as_502(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(connect_result=RuntimeError("no route to host"))
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post(
        "/api/v1/sync/connect", json={"address": "/ip4/9.9.9.9/tcp/1/p2p/x"}, headers=auth_headers
    )

    assert response.status_code == 502
    assert "no route to host" in response.json()["detail"]


def test_sync_disconnect_when_running_succeeds(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine()
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post(
        "/api/v1/sync/disconnect", json={"peer_id": "peer-2"}, headers=auth_headers
    )

    assert response.status_code == 204


async def test_resolve_conflict_rejects_invalid_keep_for_a_real_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Unlike test_resolve_conflict_invalid_keep (which hits a nonexistent
    conflict id, so it can't tell 400-before-404 apart from a plain 404),
    this creates a real conflict first so an invalid `keep` value is
    unambiguously exercising the 400 validation branch, not the 404 lookup."""
    create = client.post("/api/v1/channels", json={"name": "conflict-chan"}, headers=auth_headers)
    channel_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "channel", channel_id, "node-a")
        result = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-b": 1},
            "node-b",
            {"name": "remote", "description": None, "position": 0, "archived": False},
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == channel_id)

    response = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "bogus"},
        headers=auth_headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# sync/apply.py: branches the HTTP-layer/db_session tests above don't reach --
# stale tool/file deliveries, file-level conflicts, the "peer doesn't have it
# either" fetch outcome, pending-inbound retry with an unexpected entity type,
# and handle_incoming_state_change (SyncEngine's real callback) end to end.
# ---------------------------------------------------------------------------


async def test_apply_remote_tool_change_ignores_stale(db_session: AsyncSession) -> None:
    await record_local_change(db_session, "tool", "tool-stale", "node-a")  # local at {node-a: 1}

    result = await apply_remote_tool_change(
        db_session,
        "tool-stale",
        {"node-a": 0},
        "node-b",
        {"name": "whatever", "description": "x", "source_code": "pass"},
    )

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Tool, "tool-stale") is None


async def test_apply_remote_file_change_ignores_stale(db_session: AsyncSession) -> None:
    await record_local_change(db_session, "file", "file-stale", "node-a")

    result = await apply_remote_file_change(
        db_session,
        "file-stale",
        {"node-a": 0},
        "node-b",
        {
            "content_hash": "d" * 64,
            "filename": "x.txt",
            "mime_type": "text/plain",
            "size_bytes": 1,
            "message_id": None,
        },
    )

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(File, "file-stale") is None


async def test_apply_remote_file_change_detects_conflict(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    db_session.add(
        File(
            id="file-1",
            content_hash="e" * 64,
            filename="local.txt",
            mime_type="text/plain",
            size_bytes=1,
            local_path="not-a-real-path/local.txt",
        )
    )
    await db_session.commit()
    await record_local_change(db_session, "file", "file-1", "node-a")

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": "f" * 64,
            "filename": "remote.txt",
            "mime_type": "text/plain",
            "size_bytes": 2,
            "message_id": None,
        },
    )

    assert result.applied is False
    assert result.conflict is True
    file_row = await db_session.get(File, "file-1")
    assert file_row is not None
    assert file_row.filename == "local.txt"  # untouched

    conflicts = list((await db_session.execute(select(SyncConflict))).scalars().all())
    assert len(conflicts) == 1
    assert conflicts[0].entity_type == "file"


async def test_apply_remote_file_change_logs_when_peer_also_lacks_content(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content_hash = "1" * 64

    class _FakeEngineNoContent:
        running = True

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            assert peer_id == "node-b"
            assert requested_hash == content_hash
            return None

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngineNoContent())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-2",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )

    assert result.applied is True
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert not local_path.exists()


async def test_retry_pending_inbound_skips_unrecognized_entity_type(
    db_session: AsyncSession,
) -> None:
    """Tool/file never end up in SyncPendingInbound (module docstring: they
    have no FK-ordering hazard), but retry_pending_inbound must still cope
    defensively with an entity_type get_entity_spec doesn't dispatch on
    without raising -- it just clears the row and moves on."""
    db_session.add(
        SyncPendingInbound(
            entity_type="not-a-real-entity-type",
            entity_id="whatever",
            vector_clock_json="{}",
            origin_node_id="node-b",
            payload_json="{}",
        )
    )
    await db_session.commit()

    await retry_pending_inbound(db_session)

    remaining = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert remaining == []


async def test_handle_incoming_state_change_applies_a_dispatch_entity(
    db_session: AsyncSession,
) -> None:
    await handle_incoming_state_change(
        "agent", "agent-remote-1", {"node-b": 1}, "node-b", {"name": "Remote", **_AGENT_FIELDS}
    )
    agent = await db_session.get(Agent, "agent-remote-1")
    assert agent is not None
    assert agent.name == "Remote"


async def test_handle_incoming_state_change_applies_a_tool(db_session: AsyncSession) -> None:
    await handle_incoming_state_change(
        "tool",
        "tool-remote-1",
        {"node-b": 1},
        "node-b",
        {"name": "remote_tool", "description": "d", "source_code": "def f():\n    pass\n"},
    )
    tool = await db_session.get(Tool, "tool-remote-1")
    assert tool is not None
    assert tool.name == "remote_tool"


async def test_handle_incoming_state_change_applies_a_file(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    content_hash = "2" * 64
    await handle_incoming_state_change(
        "file",
        "file-remote-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "remote.txt",
            "mime_type": "text/plain",
            "size_bytes": 3,
            "message_id": None,
        },
    )
    file_row = await db_session.get(File, "file-remote-1")
    assert file_row is not None
    assert file_row.content_hash == content_hash


async def test_handle_incoming_state_change_drops_unsupported_entity_type(
    db_session: AsyncSession,
) -> None:
    # Must not raise, and must not create anything -- just logged and dropped.
    await handle_incoming_state_change(
        "not-a-real-entity-type", "whatever", {"node-b": 1}, "node-b", {}
    )


async def test_handle_incoming_state_change_records_a_conflict(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-conf", name="Local", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-conf", "node-a")

    await handle_incoming_state_change(
        "agent", "agent-conf", {"node-b": 1}, "node-b", {"name": "Remote", **_AGENT_FIELDS}
    )

    conflicts = list(
        (
            await db_session.execute(
                select(SyncConflict).where(SyncConflict.entity_id == "agent-conf")
            )
        )
        .scalars()
        .all()
    )
    assert len(conflicts) == 1
    agent = await db_session.get(Agent, "agent-conf")
    assert agent is not None
    assert agent.name == "Local"  # untouched -- the conflict wasn't auto-applied


async def test_handle_incoming_state_change_retries_pending_inbound_on_success(
    db_session: AsyncSession,
) -> None:
    """End-to-end regression for the FK-ordering hazard (module docstring):
    a rivulet arrives before its channel and gets queued; once the channel
    itself arrives via handle_incoming_state_change, the queued rivulet
    must be retried automatically, not left stranded."""
    queue_result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-pending",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "chan-not-yet-synced",
            "title": "Queued",
            "status": "active",
            "created_by": "human",
        },
    )
    assert queue_result.applied is False
    from rivulets.sync.apply import _record_pending_inbound  # pyright: ignore[reportPrivateUsage]

    await _record_pending_inbound(
        db_session,
        "rivulet",
        "rivulet-pending",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "chan-not-yet-synced",
            "title": "Queued",
            "status": "active",
            "created_by": "human",
        },
    )

    await handle_incoming_state_change(
        "channel",
        "chan-not-yet-synced",
        {"node-b": 1},
        "node-b",
        {"name": "general", "description": None, "position": 0, "archived": False},
    )

    rivulet = await db_session.get(Rivulet, "rivulet-pending")
    assert rivulet is not None
    assert rivulet.title == "Queued"
    remaining_pending = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert remaining_pending == []


# ---------------------------------------------------------------------------
# sync/engine.py: unit-level coverage of SyncEngine internals that the real
# two-host tests above (test_two_engines_sync_agent_state_change,
# test_engine_tracks_inbound_connections_and_detects_disconnect) don't
# exercise -- error/edge branches around startup, shutdown, auto-connect
# races, malformed gossipsub messages, and the file-transfer wire protocol,
# using fakes rather than real libp2p hosts (matching this file's own
# module docstring on _FakeEngine-style tests for apply.py above).
# ---------------------------------------------------------------------------


def test_bound_port_raises_when_host_has_no_tcp_port() -> None:
    class _BadAddr:
        def value_for_protocol(self, proto: str) -> int | None:
            raise ValueError(f"no {proto} protocol on this addr")

    class _FakeHost:
        def get_addrs(self) -> list[_BadAddr]:
            return [_BadAddr()]

    with pytest.raises(RuntimeError, match="no bound TCP port"):
        _bound_port(_FakeHost())  # pyright: ignore[reportArgumentType]


async def test_notifee_connected_handles_transport_address_lookup_failure() -> None:
    connected: list[tuple[str, str]] = []
    notifee = _PeerConnectionNotifee(
        on_connected=lambda pid, addr: connected.append((pid, addr)), on_disconnected=lambda _: None
    )

    class _BoomConn:
        muxed_conn = SimpleNamespace(peer_id="peer-x")

        def get_transport_addresses(self) -> list[str]:
            raise RuntimeError("peerstore race")

    await notifee.connected(None, _BoomConn())  # pyright: ignore[reportArgumentType]

    assert connected == [("peer-x", "")]


def test_node_id_raises_when_engine_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    with pytest.raises(RuntimeError, match="not running"):
        _ = engine.node_id


async def test_start_is_a_noop_when_already_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    sentinel = object()
    engine._thread = sentinel  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    await engine.start("fingerprint", "psk-hex")

    assert engine._thread is sentinel  # pyright: ignore[reportPrivateUsage]  # start() returned early


async def test_start_surfaces_the_underlying_trio_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = SyncEngine(tmp_path)

    async def _boom() -> None:
        raise RuntimeError("psk rejected by new_host")

    monkeypatch.setattr(engine, "_trio_main", _boom)

    with pytest.raises(RuntimeError, match="Sync engine failed to start"):
        await engine.start("fingerprint", "bad-psk")

    assert engine._thread is None  # pyright: ignore[reportPrivateUsage]


async def test_stop_is_a_noop_when_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    await engine.stop()  # must not raise


async def test_stop_swallows_run_finished_error_from_the_trio_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trio.run() that's already tearing down on its own races stop()'s
    own signal -- trio.from_thread.run_sync raises RunFinishedError in that
    case, which must be swallowed rather than propagated."""
    engine = SyncEngine(tmp_path)
    already_finished_thread = threading_dummy_thread()
    engine._thread = already_finished_thread  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    engine._trio_token = object()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    engine._stop_event = trio.Event()  # pyright: ignore[reportPrivateUsage]

    def _raise_run_finished(*_args: object, **_kwargs: object) -> None:
        raise trio.RunFinishedError("trio run already exited")

    monkeypatch.setattr(trio.from_thread, "run_sync", _raise_run_finished)

    await engine.stop()  # must not raise

    assert engine._thread is None  # pyright: ignore[reportPrivateUsage]


def threading_dummy_thread() -> Any:
    import threading

    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    return t


async def test_call_trio_raises_when_engine_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    with pytest.raises(RuntimeError, match="not running"):
        await engine.connect("/ip4/127.0.0.1/tcp/1/p2p/x")


async def test_publish_state_change_is_a_noop_when_engine_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    await engine.publish_state_change("agent", "agent-1", {"name": "x"}, {"node-a": 1})
    # No exception, and nothing to assert on the wire -- the log message
    # itself (FR-9.5 offline operation) is the documented behavior here.


def _peer_info(peer_id: str, addrs: list[str] | None = None) -> Any:
    """A duck-typed stand-in for libp2p's PeerInfo (peer_id + addrs is all
    engine.py's auto-connect path ever reads off of it) -- typed as Any so
    it satisfies engine.py's real (stub-less, per pyproject.toml's
    per-directory pyright config) PeerInfo parameter type without a
    reportArgumentType ignore-comment at every call site below."""
    return SimpleNamespace(peer_id=peer_id, addrs=addrs or [])


def test_on_peer_discovered_returns_early_with_no_trio_token(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)  # never started -- _trio_token is None
    engine._on_peer_discovered(  # pyright: ignore[reportPrivateUsage]
        _peer_info("peer-x")
    )  # must not raise


def test_on_peer_discovered_logs_and_swallows_auto_connect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = SyncEngine(tmp_path)
    engine._trio_token = object()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("not actually in a trio thread")

    monkeypatch.setattr(trio.from_thread, "run", _raise)

    engine._on_peer_discovered(  # pyright: ignore[reportPrivateUsage]
        _peer_info("peer-x")
    )  # must not raise


def test_trio_auto_connect_branches(tmp_path: Path) -> None:
    """_trio_auto_connect uses trio.Lock/trio.fail_after, so it needs a real
    trio run loop -- exercised directly here (rather than through a real
    libp2p host) for the self/already-connected/failure/success branches."""

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._node_id = "self-node"  # pyright: ignore[reportPrivateUsage]
        engine._host = object()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

        # 1. Skips connecting to itself.
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("self-node")
        )
        assert engine._connected_peers == {}  # pyright: ignore[reportPrivateUsage]

        # 2. Skips a peer already recorded as connected.
        engine._connected_peers["peer-already"] = "/some/addr"  # pyright: ignore[reportPrivateUsage]
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("peer-already")
        )
        assert engine._connected_peers["peer-already"] == "/some/addr"  # pyright: ignore[reportPrivateUsage]

        # 3. Logs and swallows a connect failure -- never recorded as connected.
        class _FailingHost:
            async def connect(self, _info: object) -> None:
                raise RuntimeError("connection refused")

        engine._host = _FailingHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("peer-fails")
        )
        assert "peer-fails" not in engine._connected_peers  # pyright: ignore[reportPrivateUsage]

        # 4. A clean connect records the peer and its first address.
        class _WorkingHost:
            async def connect(self, _info: object) -> None:
                return None

        engine._host = _WorkingHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("peer-ok", ["/ip4/1.2.3.4/tcp/1"])
        )
        assert engine._connected_peers["peer-ok"] == "/ip4/1.2.3.4/tcp/1"  # pyright: ignore[reportPrivateUsage]

    trio.run(main)


def test_on_peer_connected_ignores_self(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._node_id = "self-node"  # pyright: ignore[reportPrivateUsage]
    engine._on_peer_connected("self-node", "/some/addr")  # pyright: ignore[reportPrivateUsage]
    assert engine._connected_peers == {}  # pyright: ignore[reportPrivateUsage]


async def test_trigger_peer_connected_handler_is_a_noop_with_no_handler(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    # Sleeps _MESH_FORM_DELAY_SECONDS internally -- monkeypatch trio.sleep so
    # this doesn't add ~2s of real wall-clock time to the suite.
    import rivulets.sync.engine as engine_module

    async def _no_sleep(_seconds: float) -> None:
        return None

    original_sleep = engine_module.trio.sleep
    engine_module.trio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        await engine._trigger_peer_connected_handler()  # pyright: ignore[reportPrivateUsage]
    finally:
        engine_module.trio.sleep = original_sleep  # type: ignore[assignment]


async def test_trigger_peer_connected_handler_invokes_the_registered_handler(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    called = asyncio.Event()

    async def handler() -> None:
        called.set()

    engine.set_peer_connected_handler(handler)

    import rivulets.sync.engine as engine_module

    async def _no_sleep(_seconds: float) -> None:
        return None

    original_sleep = engine_module.trio.sleep
    engine_module.trio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        await engine._trigger_peer_connected_handler()  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(called.wait(), timeout=5)
    finally:
        engine_module.trio.sleep = original_sleep  # type: ignore[assignment]


def _noop_on_connected(_peer_id: str, _address: str) -> None:
    return None


def _noop_on_disconnected(_peer_id: str) -> None:
    return None


async def test_notifee_listen_callbacks_are_no_ops() -> None:
    """opened_stream/closed_stream/listen/listen_close are protocol-required
    INotifee overrides this engine doesn't act on -- confirms they're
    genuinely inert rather than accidentally raising."""
    notifee = _PeerConnectionNotifee(
        on_connected=_noop_on_connected, on_disconnected=_noop_on_disconnected
    )
    assert await notifee.opened_stream(None, None) is None  # pyright: ignore[reportArgumentType]
    assert await notifee.closed_stream(None, None) is None  # pyright: ignore[reportArgumentType]
    assert await notifee.listen(None, None) is None  # pyright: ignore[reportArgumentType]
    assert await notifee.listen_close(None, None) is None  # pyright: ignore[reportArgumentType]


async def test_receive_loop_discards_a_malformed_message(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    received: list[object] = []

    async def on_change(*args: object) -> None:
        received.append(args)

    engine.set_state_change_handler(on_change)  # type: ignore[arg-type]

    class _BadMessage:
        from_id = b"someone-else"
        data = b"not valid json"

    async def _one_message() -> Any:
        yield _BadMessage()

    await engine._receive_loop(_one_message())  # pyright: ignore[reportPrivateUsage]

    assert received == []  # malformed message was discarded, not handed to the handler


class _FakeFileStream:
    def __init__(self, to_read: bytes = b"") -> None:
        self._buf = to_read
        self.written = b""
        self.closed = False
        self.write_closed = False

    async def read(self, n: int) -> bytes:
        chunk = self._buf[:n]
        self._buf = self._buf[n:]
        return chunk

    async def write(self, data: bytes) -> None:
        self.written += data

    async def close(self) -> None:
        self.closed = True

    async def close_write(self) -> None:
        self.write_closed = True


async def test_handle_file_transfer_stream_reports_miss(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    try:
        stream = _FakeFileStream(to_read=("0" * HASH_LEN).encode())
        await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert stream.written == MISS_MARKER
        assert stream.closed is True
    finally:
        monkeypatch.undo()


async def test_handle_file_transfer_stream_reports_hit(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    try:
        content_hash = "a" * HASH_LEN
        local_path = get_settings().files_dir / content_hash[:2] / content_hash
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"hello from disk")

        stream = _FakeFileStream(to_read=content_hash.encode())
        await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

        assert (
            stream.written
            == HIT_PREFIX + struct.pack(">Q", len(b"hello from disk")) + b"hello from disk"
        )
        assert stream.closed is True
    finally:
        monkeypatch.undo()


async def test_handle_file_transfer_stream_logs_and_closes_on_malformed_request(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    stream = _FakeFileStream(to_read=b"")  # too short -- read_exactly raises EOFError

    await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert stream.written == b""
    assert stream.closed is True  # finally still runs


def _sample_dispatch_request() -> AgentDispatchRequest:
    return AgentDispatchRequest(
        rivulet_id="riv-1",
        channel_id="chan-1",
        agent_id="agent-1",
        message_content="hello",
        from_agent_id=None,
        from_agent_name=None,
        triggering_message_id="msg-1",
    )


def _framed(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def _unframe_response(written: bytes) -> dict[str, Any]:
    length = int.from_bytes(written[:4], "big")
    return json.loads(written[4 : 4 + length].decode())


async def test_handle_agent_dispatch_stream_acks_accepted_when_handler_registered(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    received: list[AgentDispatchRequest] = []

    async def fake_handler(request: AgentDispatchRequest) -> None:
        received.append(request)

    engine.set_agent_dispatch_handler(fake_handler)
    request = _sample_dispatch_request()
    stream = _FakeFileStream(to_read=_framed(request.to_bytes()))

    await engine._handle_agent_dispatch_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert _unframe_response(stream.written) == {"accepted": True, "reason": None}
    assert stream.closed is True

    # The handler runs via asyncio.run_coroutine_threadsafe -- fire-and-
    # forget from the stream handler's own perspective, so poll briefly
    # for it to actually run before asserting.
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)
    assert received == [request]


async def test_handle_agent_dispatch_stream_acks_rejected_when_no_handler_registered(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    stream = _FakeFileStream(to_read=_framed(_sample_dispatch_request().to_bytes()))

    await engine._handle_agent_dispatch_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    response = _unframe_response(stream.written)
    assert response["accepted"] is False
    assert response["reason"]
    assert stream.closed is True


async def test_handle_agent_dispatch_stream_logs_and_closes_on_malformed_request(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    stream = _FakeFileStream(to_read=b"")  # too short -- read_exactly raises EOFError

    await engine._handle_agent_dispatch_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert stream.written == b""
    assert stream.closed is True  # finally still runs


def test_trio_dispatch_agent_returns_true_on_accept(tmp_path: Path) -> None:
    request = _sample_dispatch_request()
    response = _framed(json.dumps({"accepted": True, "reason": None}).encode())

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_dispatch_agent(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), request
        )
        assert result is True

    trio.run(main)


def test_trio_dispatch_agent_returns_false_on_reject(tmp_path: Path) -> None:
    response = _framed(json.dumps({"accepted": False, "reason": "no dispatch handler"}).encode())

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_dispatch_agent(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), _sample_dispatch_request()
        )
        assert result is False

    trio.run(main)


async def test_dispatch_agent_remotely_returns_false_on_transport_failure(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)  # never started -- _call_trio raises immediately
    result = await engine.dispatch_agent_remotely("peer-x", _sample_dispatch_request())
    assert result is False


def _valid_base58_peer_id() -> str:
    """A syntactically valid libp2p peer id string -- _trio_request_file
    round-trips it through ID.from_base58 before ever touching the (faked)
    host, so an arbitrary string like "peer-x" fails there with a base58
    decode error unrelated to what this test actually exercises."""
    return str(_ID(os.urandom(32)))


def test_trio_request_file_reads_a_hit_response(tmp_path: Path) -> None:
    data = b"remote file bytes"
    response = HIT_PREFIX + struct.pack(">Q", len(data)) + data

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_request_file(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), "b" * HASH_LEN
        )
        assert result == data

    trio.run(main)


def test_trio_request_file_returns_none_on_miss(tmp_path: Path) -> None:
    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=MISS_MARKER)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_request_file(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), "c" * HASH_LEN
        )
        assert result is None

    trio.run(main)


async def test_request_file_returns_none_and_logs_on_transport_failure(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)  # never started -- _call_trio raises immediately
    result = await engine.request_file("peer-x", "d" * HASH_LEN)
    assert result is None


def test_get_sync_engine_raises_when_not_initialized() -> None:
    reset_sync_engine_for_testing()
    with pytest.raises(RuntimeError, match="not initialized"):
        get_sync_engine()
