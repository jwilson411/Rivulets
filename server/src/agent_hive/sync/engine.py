"""P2P sync engine (ADR-006, FR-9) — real libp2p wiring.

py-libp2p (the only maintained Python libp2p implementation) is built on
`trio`, not `asyncio` — the rest of this app (FastAPI, SQLAlchemy async,
uvicorn) is asyncio-native. Rather than mixing event loops in one thread
(fragile — trio has no first-class "run as an asyncio task" mode), the
whole libp2p host/gossipsub/mDNS stack runs in its own OS thread with its
own `trio.run()`. Two bridges connect it to the rest of the app:

  - asyncio -> trio (FastAPI route calls into the engine, e.g. `connect()`,
    `publish_state_change()`): `trio.from_thread.run(fn, trio_token=...)`,
    itself wrapped in `asyncio.to_thread(...)` so the blocking round-trip
    doesn't stall the event loop. `trio_token` is captured once via
    `trio.lowlevel.current_trio_token()` right after `trio.run()` starts —
    this is trio's documented mechanism for re-entering a run from a
    thread trio didn't spawn itself (see `trio.from_thread.run`'s
    docstring: "Pass a keyword argument, trio_token ... if you have a
    'foreign' thread"). Verified against a standalone smoke test before
    wiring it in here — 20 concurrent asyncio-side calls into the trio
    thread, no lost updates, clean shutdown, no hung thread.
  - trio -> asyncio (an incoming gossipsub message needs to be applied to
    the DB, which only has an async/aiosqlite engine): a plain
    `asyncio.run_coroutine_threadsafe(coro, loop)` from the trio thread —
    this doesn't need any trio-specific machinery since it's just a normal
    cross-thread call into a *different* asyncio loop.

This is deliberately the pattern that MCP discovery's post-mortem
(agentos/mcp.py) argued for in the abstract — isolating a foreign async
runtime's cancellation/scope machinery onto its own thread so it can't
corrupt the request-handling task's own state — except here the "foreign
runtime" is trio itself, not just a stray CancelledError, so the
isolation is structural (a whole separate thread + loop) rather than a
single `asyncio.Task`.

FR-9's full scope (FR-9.1-9.7) is large; host lifecycle, workspace-scoped
mDNS discovery, manual connect (FR-9.3), PNet pre-shared-key isolation
using the workspace key (FR-9.4 — see the note below on PNet vs. Noise),
gossipsub-based state sync for agent/channel/team/mcp_server/tool/thread/
message with vector-clock conflict detection (FR-9.6, via sync/apply.py)
are built. File content transfer (FR-9.7) rides a second, non-gossipsub
mechanism — see sync/file_transfer.py's module docstring for why a raw
point-to-point stream protocol is used instead of gossipsub for file
bytes. Not yet built: workspace settings sync, and UI conflict surfacing.

FR-9.4 note: the architecture doc describes "workspace key as PSK for the
noise handshake." Reading py-libp2p's actual noise implementation
(security/noise/patterns.py) shows it hardcodes the plain `Noise_XX_...`
pattern with no PSK modifier — there's no PSK-inside-Noise mechanism in
this library. What *is* implemented and wired end-to-end is libp2p's PNet
(private network): a shared secret applied to the raw connection
(security/pnet/protector.py) *before* Noise runs at all, gating who can
even begin a handshake. `new_host(psk=...)` accepts exactly that PSK.
Functionally this is what FR-9.4 wants (a shared secret gates the
connection); it just lives one layer lower than the doc's wording
suggests. Confirmed by testing three cases against real host pairs:
matching PSK connects, mismatched PSK fails, no PSK on either side
connects (plaintext-negotiated Noise). One operational wrinkle: a PSK
mismatch is not a fast, clean rejection — the dialing side just hangs
until timeout (the accepting side gets garbage bytes and fails a
handshake it can't attribute to a bad PSK) — hence `_CONNECT_TIMEOUT_SECONDS`
below.
"""

import asyncio
import json
import logging
import struct
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import trio
from libp2p import new_host
from libp2p.abc import IHost, INetStream
from libp2p.custom_types import TProtocol
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import PeerInfo as Libp2pPeerInfo
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.tools.anyio_service import background_trio_service
from libp2p.utils.address_validation import find_free_port, get_available_interfaces
from multiaddr import Multiaddr

from agent_hive.config import get_settings
from agent_hive.sync.discovery import WorkspaceMDNS, on_peer_discovered
from agent_hive.sync.file_transfer import (
    FILE_TRANSFER_PROTOCOL,
    HASH_LEN,
    HIT_PREFIX,
    MISS_MARKER,
    read_exactly,
)
from agent_hive.sync.identity import load_or_create_node_key

logger = logging.getLogger(__name__)

_STATE_TOPIC = "agent-hive/state/v1"
_GOSSIPSUB_PROTOCOL = TProtocol("/meshsub/1.0.0")
_CONNECT_TIMEOUT_SECONDS = 10
_THREAD_START_TIMEOUT_SECONDS = 10
_THREAD_STOP_TIMEOUT_SECONDS = 10
_FILE_TRANSFER_TIMEOUT_SECONDS = 60

StateChangeHandler = Callable[
    [str, str, dict[str, int], str, dict[str, Any]], Coroutine[Any, Any, None]
]


@dataclass(frozen=True, slots=True)
class PeerInfo:
    peer_id: str
    address: str
    connected: bool


def _bound_port(host: IHost) -> int:
    for addr in host.get_addrs():
        try:
            port = addr.value_for_protocol("tcp")
        except Exception:  # noqa: S112 - not every listen addr is TCP, just try the next one
            continue
        if port is not None:
            return int(port)
    raise RuntimeError("Host has no bound TCP port")


class SyncEngine:
    """One instance per process (single-workspace-per-node). Constructed
    at app startup but not actually running libp2p until `start()` is
    called — that needs the workspace PSK, only available after login."""

    def __init__(self, sync_dir: Path) -> None:
        self._sync_dir = sync_dir
        self._thread: threading.Thread | None = None
        self._trio_token: trio.lowlevel.TrioToken | None = None
        self._ready = threading.Event()
        self._stop_event: trio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._host: IHost | None = None
        self._pubsub: Pubsub | None = None
        self._mdns: WorkspaceMDNS | None = None
        self._connected_peers: dict[str, str] = {}
        # Serializes concurrent connect attempts to the same peer_id.
        # Manual connect() and mDNS auto-connect can both target the same
        # peer at nearly the same moment; two concurrent host.connect()
        # calls to the same peer reliably broke gossipsub mesh formation
        # (found via a real test failure once auto-connect was added, not
        # hypothetically) rather than just being redundant work. Grows one
        # entry per ever-seen peer_id and is never pruned -- fine for a
        # small mesh, not for a large/churning one.
        self._connect_locks: dict[str, trio.Lock] = {}
        self._on_state_change: StateChangeHandler | None = None
        self._workspace_fingerprint: str | None = None
        self._psk_hex: str | None = None
        self._node_id: str | None = None
        self._start_error: BaseException | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None

    @property
    def node_id(self) -> str:
        if self._node_id is None:
            raise RuntimeError("Sync engine not running")
        return self._node_id

    def set_state_change_handler(self, handler: StateChangeHandler) -> None:
        self._on_state_change = handler

    async def start(self, workspace_fingerprint: str, psk_hex: str) -> None:
        """Idempotent: a second call while already running is a no-op —
        callers should stop() first if they need to restart against a
        different workspace. `workspace_fingerprint` must be derived from
        the shared workspace key (keys.derive_workspace_fingerprint), NOT
        the Workspace DB row's id — see that function's docstring for why
        peers would never find each other via mDNS otherwise."""
        if self._thread is not None:
            return
        self._workspace_fingerprint = workspace_fingerprint
        self._psk_hex = psk_hex
        self._loop = asyncio.get_running_loop()
        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(
            target=self._run_trio, daemon=True, name="agent-hive-sync"
        )
        self._thread.start()
        ready = await asyncio.to_thread(self._ready.wait, _THREAD_START_TIMEOUT_SECONDS)
        if not ready or self._trio_token is None or self._start_error is not None:
            self._thread = None
            raise RuntimeError(f"Sync engine failed to start: {self._start_error}")

    def _run_trio(self) -> None:
        try:
            trio.run(self._trio_main)
        except BaseException as exc:  # noqa: BLE001 - surfaced to start() via _start_error
            self._start_error = exc
            self._ready.set()

    async def _trio_main(self) -> None:
        assert self._workspace_fingerprint is not None
        key_pair = load_or_create_node_key(self._sync_dir)
        self._trio_token = trio.lowlevel.current_trio_token()
        self._stop_event = trio.Event()

        host = new_host(key_pair=key_pair, psk=self._psk_hex)
        self._host = host
        self._node_id = str(host.get_id())
        host.set_stream_handler(FILE_TRANSFER_PROTOCOL, self._handle_file_transfer_stream)

        gossipsub = GossipSub(
            protocols=[_GOSSIPSUB_PROTOCOL],
            degree=6,
            degree_low=4,
            degree_high=12,
            heartbeat_initial_delay=0.5,
            heartbeat_interval=2,
        )
        pubsub = Pubsub(host, gossipsub)
        self._pubsub = pubsub

        # A wildcard "/ip4/0.0.0.0/tcp/0" listen addr binds fine but isn't a
        # dialable *address* — peers need concrete interface IPs (LAN +
        # loopback) to connect to, hence enumerating them explicitly and
        # binding all of them to one freshly-chosen port.
        port = find_free_port()
        listen_addrs = get_available_interfaces(port)
        async with host.run(listen_addrs=listen_addrs):
            mdns = WorkspaceMDNS(host, self._workspace_fingerprint, _bound_port(host))
            self._mdns = mdns
            # PeerListener (sync/discovery.py) only adds a discovered
            # peer's address to the peerstore -- it does not dial it.
            # register_peer_discovered_handler's list has no
            # unregister, so this bound method can end up registered more
            # than once across repeated start()/stop() cycles in the same
            # process; each call re-checks self._trio_token and
            # self._connected_peers fresh, so duplicate firings are
            # harmless no-ops after the first real connect, just not
            # maximally efficient.
            on_peer_discovered(self._on_peer_discovered)
            mdns.start()
            try:
                async with background_trio_service(pubsub), background_trio_service(gossipsub):
                    await pubsub.wait_until_ready()
                    subscription = await pubsub.subscribe(_STATE_TOPIC)
                    self._ready.set()
                    async with trio.open_nursery() as nursery:
                        nursery.start_soon(self._receive_loop, subscription)
                        await self._stop_event.wait()
                        nursery.cancel_scope.cancel()
            finally:
                mdns.stop()
                self._mdns = None
        self._trio_token = None
        self._host = None
        self._pubsub = None

    async def _receive_loop(self, subscription: Any) -> None:
        own_id_bytes = self._host.get_id().to_bytes() if self._host else b""
        async for message in subscription:
            if message.from_id == own_id_bytes:
                continue  # our own publish, echoed back by the mesh
            try:
                envelope = json.loads(message.data.decode())
                entity_type = envelope["entity_type"]
                entity_id = envelope["entity_id"]
                vector_clock = envelope["vector_clock"]
                origin_node_id = envelope["origin_node_id"]
                payload = envelope["payload"]
            except Exception:
                logger.warning("Discarding malformed sync message", exc_info=True)
                continue
            handler = self._on_state_change
            loop = self._loop
            if handler is not None and loop is not None:
                asyncio.run_coroutine_threadsafe(
                    handler(entity_type, entity_id, vector_clock, origin_node_id, payload), loop
                )

    def _on_peer_discovered(self, peer_info: Libp2pPeerInfo) -> None:
        """Called synchronously from zeroconf's own listener thread
        (sync/discovery.py) — bridges into the trio thread the same way
        the asyncio side does, via trio.from_thread.run with an explicit
        token, since this is yet another foreign thread as far as trio is
        concerned."""
        token = self._trio_token
        if token is None:
            return
        try:
            trio.from_thread.run(self._trio_auto_connect, peer_info, trio_token=token)
        except Exception:
            logger.warning("Auto-connect on mDNS discovery failed", exc_info=True)

    def _lock_for_peer(self, peer_id: str) -> trio.Lock:
        lock = self._connect_locks.get(peer_id)
        if lock is None:
            lock = trio.Lock()
            self._connect_locks[peer_id] = lock
        return lock

    async def _trio_auto_connect(self, peer_info: Libp2pPeerInfo) -> None:
        assert self._host is not None
        peer_id = str(peer_info.peer_id)
        if peer_id == self._node_id:
            return
        async with self._lock_for_peer(peer_id):
            if peer_id in self._connected_peers:
                return
            try:
                with trio.fail_after(_CONNECT_TIMEOUT_SECONDS):
                    await self._host.connect(peer_info)
            except Exception:
                logger.warning(
                    "Could not auto-connect to discovered peer %s", peer_id, exc_info=True
                )
                return
            address = str(peer_info.addrs[0]) if peer_info.addrs else ""
            self._connected_peers[peer_id] = address
            logger.info("Auto-connected to mDNS-discovered peer %s", peer_id)

    async def stop(self) -> None:
        if self._thread is None:
            return
        token = self._trio_token
        stop_event = self._stop_event
        if token is not None and stop_event is not None:

            def _signal() -> None:
                stop_event.set()

            try:
                await asyncio.to_thread(trio.from_thread.run_sync, _signal, trio_token=token)
            except trio.RunFinishedError:
                pass  # trio run already tearing down on its own
        await asyncio.to_thread(self._thread.join, _THREAD_STOP_TIMEOUT_SECONDS)
        self._thread = None
        self._connected_peers.clear()

    async def _call_trio(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        token = self._trio_token
        if token is None:
            raise RuntimeError("Sync engine not running")
        return await asyncio.to_thread(trio.from_thread.run, fn, *args, trio_token=token)

    async def connect(self, address: str) -> PeerInfo:
        return await self._call_trio(self._trio_connect, address)

    async def _trio_connect(self, address: str) -> PeerInfo:
        assert self._host is not None
        info = info_from_p2p_addr(Multiaddr(address))
        peer_id = str(info.peer_id)
        async with self._lock_for_peer(peer_id):
            if peer_id not in self._connected_peers:
                with trio.fail_after(_CONNECT_TIMEOUT_SECONDS):
                    await self._host.connect(info)
                self._connected_peers[peer_id] = address
        return PeerInfo(peer_id=peer_id, address=address, connected=True)

    async def disconnect(self, peer_id: str) -> None:
        await self._call_trio(self._trio_disconnect, peer_id)

    async def _trio_disconnect(self, peer_id: str) -> None:
        assert self._host is not None
        await self._host.disconnect(ID.from_base58(peer_id))
        self._connected_peers.pop(peer_id, None)

    async def list_peers(self) -> list[PeerInfo]:
        return [
            PeerInfo(peer_id=pid, address=addr, connected=True)
            for pid, addr in self._connected_peers.items()
        ]

    async def publish_state_change(
        self,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        vector_clock: dict[str, int],
    ) -> None:
        if self._thread is None:
            logger.info(
                "Sync engine not running — %s/%s change not published (FR-9.5 offline operation)",
                entity_type,
                entity_id,
            )
            return
        envelope = json.dumps(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "vector_clock": vector_clock,
                "origin_node_id": self.node_id,
                "payload": payload,
            }
        ).encode()
        await self._call_trio(self._trio_publish, envelope)

    async def _trio_publish(self, envelope: bytes) -> None:
        assert self._pubsub is not None
        await self._pubsub.publish(_STATE_TOPIC, envelope)

    async def _handle_file_transfer_stream(self, stream: INetStream) -> None:
        """Server side of the file_transfer.py wire protocol — set as this
        host's handler for FILE_TRANSFER_PROTOCOL at host creation. Errors
        here (a malformed request, a peer disconnecting mid-read) are
        logged and swallowed rather than raised: this runs as a background
        task per incoming stream, with nothing upstream to propagate to."""
        try:
            content_hash = (await read_exactly(stream, HASH_LEN)).decode()
            local_path = get_settings().files_dir / content_hash[:2] / content_hash
            if not local_path.exists():
                await stream.write(MISS_MARKER)
            else:
                data = local_path.read_bytes()
                await stream.write(HIT_PREFIX + struct.pack(">Q", len(data)) + data)
        except Exception:
            logger.warning("Error handling file-transfer stream request", exc_info=True)
        finally:
            await stream.close()

    async def request_file(self, peer_id: str, content_hash: str) -> bytes | None:
        """Client side: ask `peer_id` for `content_hash`'s bytes. Returns
        None on a clean MISS (peer doesn't have it either) or on any
        transport failure (peer unreachable, timeout, protocol error) —
        callers can't distinguish those cases from this return value alone,
        but both mean the same thing to a caller: content not available
        from this peer right now."""
        try:
            return await self._call_trio(self._trio_request_file, peer_id, content_hash)
        except Exception:
            logger.warning(
                "Could not fetch file content (hash=%s) from peer %s",
                content_hash[:12],
                peer_id,
                exc_info=True,
            )
            return None

    async def _trio_request_file(self, peer_id: str, content_hash: str) -> bytes | None:
        assert self._host is not None
        stream = await self._host.new_stream(ID.from_base58(peer_id), [FILE_TRANSFER_PROTOCOL])
        try:
            with trio.fail_after(_FILE_TRANSFER_TIMEOUT_SECONDS):
                await stream.write(content_hash.encode())
                # close_write() is on the concrete NetStream (verified
                # against a real stream in scratch testing) but missing
                # from the INetStream ABC that new_stream()'s return type
                # is declared as -- a stub-completeness gap, not a real
                # attribute error.
                await stream.close_write()  # pyright: ignore[reportAttributeAccessIssue]
                marker = await read_exactly(stream, 4)
                if marker == MISS_MARKER:
                    return None
                (length,) = struct.unpack(">Q", await read_exactly(stream, 8))
                return await read_exactly(stream, length)
        finally:
            await stream.close()


_engine: SyncEngine | None = None


def init_sync_engine(sync_dir: Path) -> SyncEngine:
    global _engine
    if _engine is None:
        _engine = SyncEngine(sync_dir)
    return _engine


def get_sync_engine() -> SyncEngine:
    if _engine is None:
        raise RuntimeError("Sync engine not initialized — call init_sync_engine() at startup")
    return _engine


def reset_sync_engine_for_testing() -> None:
    global _engine
    _engine = None
