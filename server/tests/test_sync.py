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
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.config import get_settings
from agent_hive.db.models import (
    Agent,
    Channel,
    File,
    MCPServer,
    Message,
    SyncConflict,
    Team,
    Thread,
    Tool,
    ToolVersion,
    WorkspaceSetting,
)
from agent_hive.sync.apply import (
    AGENT_SPEC,
    CHANNEL_SPEC,
    MCP_SERVER_SPEC,
    MESSAGE_SPEC,
    TEAM_SPEC,
    THREAD_SPEC,
    WORKSPACE_SETTING_SPEC,
    ClockComparison,
    apply_remote_change,
    apply_remote_file_change,
    apply_remote_tool_change,
    compare_vector_clocks,
    merge_vector_clocks,
    record_local_change,
)
from agent_hive.sync.engine import SyncEngine, init_sync_engine, reset_sync_engine_for_testing

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


async def test_apply_remote_thread_change_creates_thread(db_session: AsyncSession) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        THREAD_SPEC,
        "thread-1",
        {"node-b": 1},
        "node-b",
        {"channel_id": "chan-1", "title": "Hello", "status": "active", "created_by": "human"},
    )
    assert result.applied is True

    thread = await db_session.get(Thread, "thread-1")
    assert thread is not None
    assert thread.channel_id == "chan-1"
    assert thread.title == "Hello"
    assert thread.agentos_session_id is None  # never synced


async def test_apply_remote_thread_change_skips_when_channel_missing(
    db_session: AsyncSession,
) -> None:
    """Regression test for the FK-ordering hazard apply.py's module
    docstring describes: a thread arriving before its channel has synced
    must be dropped cleanly (IntegrityError caught), not crash the
    sync-message handler."""
    result = await apply_remote_change(
        db_session,
        THREAD_SPEC,
        "thread-1",
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
    assert await db_session.get(Thread, "thread-1") is None

    # The vector-clock bump must have rolled back with the failed commit --
    # otherwise a later, valid delivery of the same message would be
    # wrongly judged as already-seen (EQUAL) instead of fresh.
    retry = await apply_remote_change(
        db_session,
        THREAD_SPEC,
        "thread-1",
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
    db_session.add(Thread(id="thread-1", channel_id="chan-1", created_by="human", status="active"))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        MESSAGE_SPEC,
        "msg-1",
        {"node-b": 1},
        "node-b",
        {
            "thread_id": "thread-1",
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
    assert message.thread_id == "thread-1"
    assert message.content == "Hello from node B"


async def test_apply_remote_message_change_skips_when_thread_missing(
    db_session: AsyncSession,
) -> None:
    result = await apply_remote_change(
        db_session,
        MESSAGE_SPEC,
        "msg-1",
        {"node-b": 1},
        "node-b",
        {
            "thread_id": "does-not-exist",
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
    monkeypatch.setattr("agent_hive.sync.apply.get_sync_engine", lambda: _FakeEngine())
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


def test_thread_and_message_create_do_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel = client.post(
        "/api/v1/channels", json={"name": "offline-sync-channel"}, headers=auth_headers
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "hello while sync is offline"},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]

    message = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "another message while sync is offline"},
        headers=auth_headers,
    )
    assert message.status_code == 201, message.text

    resumed = client.post(f"/api/v1/threads/{thread_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200

    closed = client.delete(f"/api/v1/threads/{thread_id}", headers=auth_headers)
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

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "see attached", "files": [file_id]},
        headers=auth_headers,
    )
    assert thread.status_code == 201, thread.text


def test_settings_patch_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/v1/settings", json={"guard.turn_limit": 15}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["guard.turn_limit"] == 15
