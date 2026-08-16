"""Snapshot-based catch-up sync (#347) — sync/state_snapshot.py's wire
protocol, sync/catchup.py's snapshot building/pushing, and the engine's
send/receive plumbing.

The headline scenario under test: gossip only reaches peers present at
publish time, so a second machine joining with the same recovery phrase
(or a peer that was offline during a delete) must receive already-
published state via the connect-time/periodic snapshot push instead.
The receiving side is deliberately NOT re-tested entity-by-entity here:
every snapshot envelope goes through the exact same
handle_incoming_state_change path test_sync.py already covers for live
gossip messages — what's covered here is that the envelopes exist, are
correct, and arrive."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import (
    Agent,
    SyncPendingInbound,
    SyncPendingOutbound,
    SyncState,
    Tool,
    VectorClockTracker,
)
from rivulets.db.session import session_scope
from rivulets.sync.apply import TOMBSTONE_FIELD, record_local_change
from rivulets.sync.catchup import (
    build_snapshot_envelopes,
    push_snapshot_to_all_peers,
    push_snapshot_to_peer,
)
from rivulets.sync.engine import PeerInfo, SyncEngine
from rivulets.sync.state_snapshot import read_snapshot_frames, write_snapshot_frame

_AGENT_FIELDS = {
    "description": "A test agent for catch-up tests.",
    "instructions": "Do the thing.",
    "model": "openai:gpt-4",
}


# ---------------------------------------------------------------------------
# Wire protocol (state_snapshot.py)
# ---------------------------------------------------------------------------


class _FakeStream:
    """Same duck-typed INetStream stand-in test_file_transfer.py and
    test_agent_dispatch.py use — under test is the framing discipline,
    not network I/O."""

    def __init__(self, data: bytes = b"") -> None:
        self._buf = bytearray(data)

    async def read(self, n: int) -> bytes:
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    async def write(self, data: bytes) -> None:
        self._buf.extend(data)

    async def close(self) -> None:
        return None


async def test_snapshot_frames_roundtrip_until_eof() -> None:
    stream = _FakeStream()
    await write_snapshot_frame(stream, b'{"entity_type": "agent"}')  # type: ignore[arg-type]
    await write_snapshot_frame(stream, b'{"entity_type": "channel"}')  # type: ignore[arg-type]
    frames = await read_snapshot_frames(stream)  # type: ignore[arg-type]
    assert frames == [b'{"entity_type": "agent"}', b'{"entity_type": "channel"}']


async def test_read_snapshot_frames_empty_stream_is_an_empty_snapshot() -> None:
    assert await read_snapshot_frames(_FakeStream()) == []  # type: ignore[arg-type]


async def test_read_snapshot_frames_rejects_frame_above_cap_before_body_read() -> None:
    """A peer advertising ~4GB must be rejected from the prefix alone —
    the fake holds only the prefix, so an attempted body read would
    surface as EOFError, not the asserted ValueError."""
    stream = _FakeStream(b"\xff\xff\xff\xff")
    with pytest.raises(ValueError, match=r"exceeds \d+ byte limit"):
        await read_snapshot_frames(stream)  # type: ignore[arg-type]


async def test_read_snapshot_frames_rejects_total_above_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rivulets.sync.state_snapshot.MAX_SNAPSHOT_TOTAL_BYTES", 10)
    stream = _FakeStream()
    await write_snapshot_frame(stream, b"12345678")  # type: ignore[arg-type]
    await write_snapshot_frame(stream, b"12345678")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="total limit"):
        await read_snapshot_frames(stream)  # type: ignore[arg-type]


async def test_read_snapshot_frames_raises_on_truncated_frame() -> None:
    """A stream that dies mid-frame must raise, never silently return a
    partial snapshot as if it were complete."""
    stream = _FakeStream((100).to_bytes(4, "big") + b"only a few bytes")
    with pytest.raises(EOFError):
        await read_snapshot_frames(stream)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Snapshot building (catchup.build_snapshot_envelopes)
# ---------------------------------------------------------------------------


def _decode(envelopes: list[bytes]) -> list[dict[str, Any]]:
    return [json.loads(e.decode()) for e in envelopes]


async def test_build_snapshot_includes_live_entity_with_full_clock(
    db_session: AsyncSession,
) -> None:
    db_session.add(Agent(id="agent-1", name="Synced", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")
    await record_local_change(db_session, "agent", "agent-1", "node-a")

    envelopes = _decode(await build_snapshot_envelopes(db_session, "node-self"))

    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope["entity_type"] == "agent"
    assert envelope["entity_id"] == "agent-1"
    assert envelope["vector_clock"] == {"node-a": 2}
    assert envelope["origin_node_id"] == "node-self"
    assert envelope["payload"]["name"] == "Synced"
    assert envelope["payload"]["model"] == _AGENT_FIELDS["model"]


async def test_build_snapshot_emits_durable_tombstone_for_deleted_entity(
    db_session: AsyncSession,
) -> None:
    """The #347 delete case: the entity row is gone but its clock rows
    remain (apply_remote_delete never removes them) — the snapshot replays
    that as a tombstone envelope, so a peer that was offline during the
    delete finally drops the row instead of keeping it forever."""
    await record_local_change(db_session, "agent", "deleted-agent", "node-a")

    envelopes = _decode(await build_snapshot_envelopes(db_session, "node-self"))

    assert len(envelopes) == 1
    assert envelopes[0]["entity_id"] == "deleted-agent"
    assert envelopes[0]["payload"] == {TOMBSTONE_FIELD: True}
    assert envelopes[0]["vector_clock"] == {"node-a": 1}


async def test_build_snapshot_never_tombstones_a_live_but_unpublishable_entity(
    db_session: AsyncSession,
) -> None:
    """A non-custom Tool has no publishable payload (build_entity_payload
    returns None), but it exists — clock rows for one shouldn't occur, yet
    if they ever do, emitting a tombstone would delete a live tool on
    every peer. Skipping is the only safe answer."""
    db_session.add(Tool(id="tool-1", name="search", description="mcp tool", tool_type="mcp"))
    await db_session.commit()
    await record_local_change(db_session, "tool", "tool-1", "node-a")

    assert await build_snapshot_envelopes(db_session, "node-self") == []


async def test_build_snapshot_skips_unknown_entity_types(db_session: AsyncSession) -> None:
    db_session.add(
        VectorClockTracker(entity_type="not-a-real-type", entity_id="x", node_id="node-a", clock=1)
    )
    await db_session.commit()

    assert await build_snapshot_envelopes(db_session, "node-self") == []


async def test_build_snapshot_skips_oversized_envelope(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Agent(id="agent-1", name="Big", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")
    monkeypatch.setattr("rivulets.sync.catchup.MAX_SNAPSHOT_FRAME_BYTES", 10)

    assert await build_snapshot_envelopes(db_session, "node-self") == []


# ---------------------------------------------------------------------------
# Snapshot pushing (catchup.push_snapshot_to_peer / _to_all_peers)
# ---------------------------------------------------------------------------


class _FakeEngine:
    def __init__(
        self,
        *,
        running: bool = True,
        send_ok: bool = True,
        peers: list[PeerInfo] | None = None,
    ) -> None:
        self.running = running
        self.node_id = "node-self"
        self._send_ok = send_ok
        self._peers = peers or []
        self.sent: list[tuple[str, list[bytes]]] = []

    async def send_state_snapshot(self, peer_id: str, envelopes: list[bytes]) -> bool:
        self.sent.append((peer_id, envelopes))
        return self._send_ok

    async def list_peers(self) -> list[PeerInfo]:
        return self._peers


async def test_push_snapshot_sends_and_records_sync_state(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Agent(id="agent-1", name="Synced", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")
    fake = _FakeEngine()
    monkeypatch.setattr("rivulets.sync.catchup.get_sync_engine", lambda: fake)

    assert await push_snapshot_to_peer(db_session, "peer-1") is True

    assert len(fake.sent) == 1
    peer_id, envelopes = fake.sent[0]
    assert peer_id == "peer-1"
    assert _decode(envelopes)[0]["entity_id"] == "agent-1"
    state = await db_session.get(SyncState, "peer-1")
    assert state is not None
    assert state.last_sync_at is not None


async def test_push_snapshot_returns_false_and_records_nothing_on_send_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await record_local_change(db_session, "agent", "agent-1", "node-a")
    fake = _FakeEngine(send_ok=False)
    monkeypatch.setattr("rivulets.sync.catchup.get_sync_engine", lambda: fake)

    assert await push_snapshot_to_peer(db_session, "peer-1") is False
    assert await db_session.get(SyncState, "peer-1") is None


async def test_push_snapshot_noop_when_engine_not_running(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine(running=False)
    monkeypatch.setattr("rivulets.sync.catchup.get_sync_engine", lambda: fake)

    assert await push_snapshot_to_peer(db_session, "peer-1") is False
    assert fake.sent == []


async def test_push_snapshot_with_nothing_to_send_skips_the_stream(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty workspace has nothing to offer — success without ever
    opening a stream, but the sync time is still recorded (the peer IS
    up to date with our nothing)."""
    fake = _FakeEngine()
    monkeypatch.setattr("rivulets.sync.catchup.get_sync_engine", lambda: fake)

    assert await push_snapshot_to_peer(db_session, "peer-1") is True
    assert fake.sent == []
    assert await db_session.get(SyncState, "peer-1") is not None


async def test_push_snapshot_to_all_peers_builds_once_and_sends_to_each(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Agent(id="agent-1", name="Synced", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")
    fake = _FakeEngine(
        peers=[
            PeerInfo(peer_id="peer-1", address="/ip4/10.0.0.1/tcp/1", connected=True),
            PeerInfo(peer_id="peer-2", address="/ip4/10.0.0.2/tcp/1", connected=True),
        ]
    )
    monkeypatch.setattr("rivulets.sync.catchup.get_sync_engine", lambda: fake)

    await push_snapshot_to_all_peers(db_session)

    assert [peer_id for peer_id, _ in fake.sent] == ["peer-1", "peer-2"]
    assert fake.sent[0][1] == fake.sent[1][1]  # built once, reused


# ---------------------------------------------------------------------------
# Engine receive plumbing (_dispatch_snapshot_frames)
# ---------------------------------------------------------------------------


def _envelope_frame(entity_id: str) -> bytes:
    return json.dumps(
        {
            "entity_type": "agent",
            "entity_id": entity_id,
            "vector_clock": {"node-a": 1},
            "origin_node_id": "node-a",
            "payload": {"name": entity_id},
        }
    ).encode()


async def test_dispatch_snapshot_frames_applies_sequentially_in_order(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    received: list[str] = []
    done = asyncio.Event()

    async def on_change(
        entity_type: str,
        entity_id: str,
        vector_clock: dict[str, int],
        origin_node_id: str,
        payload: dict[str, Any],
    ) -> None:
        received.append(entity_id)
        if len(received) == 2:
            done.set()

    engine.set_state_change_handler(on_change)
    # A malformed frame in the middle is discarded without aborting the
    # rest of the snapshot.
    engine._dispatch_snapshot_frames(  # pyright: ignore[reportPrivateUsage]
        [_envelope_frame("agent-1"), b"not valid json", _envelope_frame("agent-2")]
    )

    await asyncio.wait_for(done.wait(), timeout=5)
    assert received == ["agent-1", "agent-2"]


async def test_dispatch_snapshot_frames_is_a_noop_with_no_handler(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    engine._dispatch_snapshot_frames(  # pyright: ignore[reportPrivateUsage]
        [_envelope_frame("agent-1")]
    )


# ---------------------------------------------------------------------------
# End-to-end over real libp2p hosts
# ---------------------------------------------------------------------------


async def _get_first_addr(engine: SyncEngine) -> str:
    host = engine._host  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert host is not None
    return str(host.get_addrs()[0])


async def test_snapshot_send_delivers_envelopes_to_peer(tmp_path: Path) -> None:
    """The full wire path with two real hosts: B (the established node)
    streams a snapshot to A, and A's state-change handler — the same one
    gossip messages hit — receives the envelope. Same PSK, different mDNS
    fingerprints, matching the other two-engine tests."""
    psk_hex = hashlib.sha256(b"catchup-e2e-workspace").digest().hex()
    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    received: list[tuple[str, str, dict[str, int], str, dict[str, Any]]] = []
    got = asyncio.Event()

    async def on_change(
        entity_type: str,
        entity_id: str,
        vector_clock: dict[str, int],
        origin_node_id: str,
        payload: dict[str, Any],
    ) -> None:
        received.append((entity_type, entity_id, vector_clock, origin_node_id, payload))
        got.set()

    engine_a.set_state_change_handler(on_change)
    await engine_a.start("catchup-e2e-fingerprint-a", psk_hex)
    await engine_b.start("catchup-e2e-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)

        envelope = json.dumps(
            {
                "entity_type": "agent",
                "entity_id": "agent-from-b",
                "vector_clock": {engine_b.node_id: 1},
                "origin_node_id": engine_b.node_id,
                "payload": {"name": "Caught Up"},
            }
        ).encode()
        assert await engine_b.send_state_snapshot(engine_a.node_id, [envelope]) is True

        await asyncio.wait_for(got.wait(), timeout=10)
        assert received == [
            (
                "agent",
                "agent-from-b",
                {engine_b.node_id: 1},
                engine_b.node_id,
                {"name": "Caught Up"},
            )
        ]
    finally:
        await engine_a.stop()
        await engine_b.stop()


async def test_send_state_snapshot_returns_false_when_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    assert await engine.send_state_snapshot("peer-1", [b"{}"]) is False


# ---------------------------------------------------------------------------
# /sync/status pending_changes (#347: no longer hardcoded 0)
# ---------------------------------------------------------------------------


async def test_sync_status_reports_real_pending_backlog(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Not asserted against an absolute count: workspace bootstrap itself
    # queues outbound rows under this fixture (the engine never runs, so
    # every starter-content publish lands in SyncPendingOutbound) — which
    # is itself the honest-backlog behavior under test.
    before = client.get("/api/v1/sync/status", headers=auth_headers).json()["pending_changes"]
    async with session_scope() as db:
        db.add(SyncPendingOutbound(entity_type="agent", entity_id="agent-1"))
        db.add(SyncPendingOutbound(entity_type="channel", entity_id="chan-1", deleted=True))
        db.add(
            SyncPendingInbound(
                entity_type="rivulet",
                entity_id="riv-1",
                vector_clock_json="{}",
                origin_node_id="node-b",
                payload_json="{}",
            )
        )
        await db.commit()

    response = client.get("/api/v1/sync/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["pending_changes"] == before + 3
