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
gossipsub-based state sync for agent/channel/team/mcp_server/tool/rivulet/
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
import ipaddress
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
from libp2p.abc import IHost, INetConn, INetStream, INetwork, INotifee
from libp2p.custom_types import TProtocol
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import PeerInfo as Libp2pPeerInfo
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.tools.anyio_service import background_trio_service
from libp2p.utils.address_validation import find_free_port, get_available_interfaces
from multiaddr import Multiaddr

from rivulets.config import get_settings
from rivulets.sync.agent_dispatch import (
    AGENT_DISPATCH_PROTOCOL,
    AgentDispatchRequest,
    read_framed,
    write_framed,
)
from rivulets.sync.coordinator import compute_capability_score, outranks
from rivulets.sync.discovery import WorkspaceMDNS, on_peer_discovered
from rivulets.sync.file_transfer import (
    FILE_TRANSFER_PROTOCOL,
    HASH_LEN,
    HIT_PREFIX,
    MISS_MARKER,
    read_exactly,
)
from rivulets.sync.identity import load_or_create_node_key

logger = logging.getLogger(__name__)

_STATE_TOPIC = "rivulets/state/v1"
_GOSSIPSUB_PROTOCOL = TProtocol("/meshsub/1.0.0")
_CONNECT_TIMEOUT_SECONDS = 10
_THREAD_START_TIMEOUT_SECONDS = 10
_THREAD_STOP_TIMEOUT_SECONDS = 10
_FILE_TRANSFER_TIMEOUT_SECONDS = 60
_AGENT_DISPATCH_ACK_TIMEOUT_SECONDS = 10

StateChangeHandler = Callable[
    [str, str, dict[str, int], str, dict[str, Any]], Coroutine[Any, Any, None]
]
PeerConnectedHandler = Callable[[], Coroutine[Any, Any, None]]
AgentDispatchHandler = Callable[[AgentDispatchRequest], Coroutine[Any, Any, None]]

_MESH_FORM_DELAY_SECONDS = 2.0

# #101: bully-style coordinator election tuning. The timeout is a multiple
# of the heartbeat interval (3 missed heartbeats), not an independent
# number, so the two can't accidentally be set inconsistently by a caller
# overriding only one of them (tests override both together instead).
_COORDINATOR_HEARTBEAT_INTERVAL_SECONDS = 5.0
_COORDINATOR_TIMEOUT_SECONDS = _COORDINATOR_HEARTBEAT_INTERVAL_SECONDS * 3


@dataclass(frozen=True, slots=True)
class PeerInfo:
    peer_id: str
    address: str
    connected: bool


@dataclass(frozen=True, slots=True)
class CoordinatorStatus:
    coordinator_id: str | None
    term: int
    is_self: bool
    self_score: float
    peer_scores: dict[str, float]


def _peer_ip(address: str) -> str | None:
    """Extracts the ip4/ip6 host component from a peer's stored multiaddr
    string (e.g. "/ip4/192.168.1.5/tcp/4001/p2p/Qm..."). Returns None for
    an empty/malformed address (see _PeerConnectionNotifee's docstring on
    why a connected peer's address can be a best-effort empty string)."""
    if not address:
        return None
    try:
        maddr = Multiaddr(address)
    except Exception:
        return None
    for proto in ("ip4", "ip6"):
        try:
            value = maddr.value_for_protocol(proto)
        except Exception:  # noqa: S112 - not every addr has this protocol, just try the next one
            continue
        if value:
            return str(value)
    return None


def _is_lan_address(address: str) -> bool:
    """LAN vs WAN classification for issue #123's eager-sync settings:
    private/loopback/link-local ranges (RFC1918, RFC4193 IPv6 ULA, mDNS-
    discovered peers are always in one of these) count as LAN; anything
    else — including an address we couldn't parse at all — is treated as
    WAN, matching sync.eager_files_wan's more conservative default
    (False) rather than silently eager-pushing to an unknown network."""
    ip = _peer_ip(address)
    if ip is None:
        return False
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _bound_port(host: IHost) -> int:
    for addr in host.get_addrs():
        try:
            port = addr.value_for_protocol("tcp")
        except Exception:  # noqa: S112 - not every listen addr is TCP, just try the next one
            continue
        if port is not None:
            return int(port)
    raise RuntimeError("Host has no bound TCP port")


class _PeerConnectionNotifee(INotifee):
    """Tracks real connection state for ALL connections on this host, not
    just ones this engine dialed itself. Two bugs this fixes together:

    1. `_connected_peers` was only ever written by _trio_connect/
       _trio_auto_connect — both outbound-only. A peer connecting TO this
       host (inbound — e.g. it dialed us, or mDNS discovery ran on their
       side first) was never recorded at all, so list_peers() silently
       missed real, active connections.
    2. Once recorded, an entry only ever left `_connected_peers` via this
       engine's own explicit disconnect() or stop() — a network drop or
       the peer's process exiting left a phantom "connected: true" entry
       forever.

    Validated against a real two-host test before wiring in:
    disconnected() fires on the side that did NOT initiate the close
    (fixes #2), and connected() fires on the accepting side of an
    inbound connection it never dialed (fixes #1).

    Address extraction on connect is best-effort: conn.get_transport_
    addresses() returned an empty list with an internal warning when
    tested immediately at connected()-time (a peerstore-population race,
    not something worth fighting) — on_connected's caller only uses this
    to fill in an address string for display, never as something
    connect()/request_file() depend on, so an empty string here is a
    cosmetic gap, not a correctness one."""

    def __init__(
        self, on_connected: Callable[[str, str], None], on_disconnected: Callable[[str], None]
    ) -> None:
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

    async def opened_stream(self, network: INetwork, stream: INetStream) -> None:
        pass

    async def closed_stream(self, network: INetwork, stream: INetStream) -> None:
        pass

    async def connected(self, network: INetwork, conn: INetConn) -> None:
        try:
            addrs = conn.get_transport_addresses()
        except Exception:
            addrs = []
        address = str(addrs[0]) if addrs else ""
        self._on_connected(str(conn.muxed_conn.peer_id), address)

    async def disconnected(self, network: INetwork, conn: INetConn) -> None:
        self._on_disconnected(str(conn.muxed_conn.peer_id))

    async def listen(self, network: INetwork, multiaddr: Multiaddr) -> None:
        pass

    async def listen_close(self, network: INetwork, multiaddr: Multiaddr) -> None:
        pass


class SyncEngine:
    """One instance per process (single-workspace-per-node). Constructed
    at app startup but not actually running libp2p until `start()` is
    called — that needs the workspace PSK, only available after login."""

    def __init__(
        self,
        sync_dir: Path,
        *,
        coordinator_heartbeat_interval: float = _COORDINATOR_HEARTBEAT_INTERVAL_SECONDS,
        coordinator_timeout: float = _COORDINATOR_TIMEOUT_SECONDS,
    ) -> None:
        self._sync_dir = sync_dir
        self._coordinator_heartbeat_interval = coordinator_heartbeat_interval
        self._coordinator_timeout = coordinator_timeout
        self._thread: threading.Thread | None = None
        self._trio_token: trio.lowlevel.TrioToken | None = None
        self._ready = threading.Event()
        self._stop_event: trio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._host: IHost | None = None
        self._pubsub: Pubsub | None = None
        self._mdns: WorkspaceMDNS | None = None
        self._connected_peers: dict[str, str] = {}
        # Other peers' self-declared capability tags (issue #10), keyed by
        # peer_id -- populated from "node_capabilities" envelopes on the
        # same _STATE_TOPIC gossipsub channel as entity sync, but this is
        # live broadcast state, not a DB-backed EntitySpec (see
        # _receive_loop): no vector clock, no apply.py, latest-wins.
        self._peer_capabilities: dict[str, list[str]] = {}
        # #101 coordinator election state -- ephemeral, in-memory only,
        # same "not a DB-backed EntitySpec" treatment as _peer_capabilities
        # above (see _receive_loop's coordinator_heartbeat branch). Reset
        # in stop() rather than persisted: trio.current_time() restarts
        # from ~0 on every _trio_main() run (a fresh trio.run() call per
        # start()), so a stale _last_coordinator_heartbeat_at from a prior
        # session would read as "from the future" relative to the new
        # session's clock and could never trip the timeout check again.
        self._coordinator_id: str | None = None
        self._coordinator_term: int = 0
        self._self_score: float = 0.0
        self._peer_scores: dict[str, float] = {}
        self._last_coordinator_heartbeat_at: float | None = None
        self._started_monotonic: float | None = None
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
        self._on_peer_connected_handler: PeerConnectedHandler | None = None
        self._on_agent_dispatch: AgentDispatchHandler | None = None
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

    def set_peer_connected_handler(self, handler: PeerConnectedHandler) -> None:
        """Called (after a short delay — see _on_peer_connected) whenever
        a *new* peer connects, mDNS-discovered or manually connected.
        api/auth.py's login wires this to drain_pending_outbound: draining
        once at login time isn't enough on its own — mDNS discovery +
        gossipsub mesh formation (heartbeat-driven GRAFT) both take a few
        seconds after the engine starts, so a drain that runs immediately
        at login publishes into a mesh with no peers yet and the message
        is simply never delivered (confirmed by a real two-node test: the
        pending-outbound row was correctly cleared and record_local_change
        bumped, but the peer never received anything). Retrying again once
        an actual peer is connected is what makes the retry meaningful."""
        self._on_peer_connected_handler = handler

    def set_agent_dispatch_handler(self, handler: AgentDispatchHandler) -> None:
        """Wired to dispatch/service.py's invoke_agent_remotely at app
        startup, same pattern as set_state_change_handler. Registering this
        is what makes _handle_agent_dispatch_stream ack incoming requests
        as accepted -- see agent_dispatch.py's module docstring for why the
        ack itself doesn't do a deeper DB-backed check."""
        self._on_agent_dispatch = handler

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
        self._thread = threading.Thread(target=self._run_trio, daemon=True, name="rivulets-sync")
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
        self._started_monotonic = trio.current_time()
        host.set_stream_handler(FILE_TRANSFER_PROTOCOL, self._handle_file_transfer_stream)
        host.set_stream_handler(AGENT_DISPATCH_PROTOCOL, self._handle_agent_dispatch_stream)
        host.get_network().register_notifee(
            _PeerConnectionNotifee(self._on_peer_connected, self._on_peer_disconnected)
        )

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
                        nursery.start_soon(self._coordinator_loop)
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
            if entity_type == "node_capabilities":
                # Not a DB-backed entity -- never reaches apply.py. Latest
                # broadcast simply replaces whatever this node had cached
                # for that peer.
                self._peer_capabilities[origin_node_id] = payload.get("capabilities", [])
                continue
            if entity_type == "coordinator_heartbeat":
                self._handle_coordinator_heartbeat(origin_node_id, payload)
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
        # See _coordinator_id's __init__ comment: must reset, not carry
        # over -- trio.current_time() restarts near-0 on the next start().
        self._coordinator_id = None
        self._coordinator_term = 0
        self._self_score = 0.0
        self._peer_scores.clear()
        self._last_coordinator_heartbeat_at = None
        self._started_monotonic = None
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

    def _on_peer_connected(self, peer_id: str, address: str) -> None:
        """_PeerConnectionNotifee's callback for both inbound connections
        (this is the only place those get recorded) and outbound ones
        (redundant with _trio_connect/_trio_auto_connect's own recording,
        which is kept because it has the real dialed address in hand
        immediately — this only overwrites it if we don't already have a
        non-empty address, so a good outbound address is never clobbered
        by an inbound accept's best-effort/empty one). Runs synchronously
        inside the trio thread's own event handling — plain dict
        mutation, no bridging needed."""
        if peer_id == self._node_id:
            return
        is_new = peer_id not in self._connected_peers
        existing = self._connected_peers.get(peer_id)
        if existing is None or (not existing and address):
            self._connected_peers[peer_id] = address
            logger.info("Peer %s connected", peer_id)
        if is_new:
            trio.lowlevel.spawn_system_task(self._trigger_peer_connected_handler)

    def _on_peer_disconnected(self, peer_id: str) -> None:
        if self._connected_peers.pop(peer_id, None) is not None:
            logger.info("Peer %s disconnected", peer_id)
        self._peer_capabilities.pop(peer_id, None)
        self._peer_scores.pop(peer_id, None)
        if peer_id == self._coordinator_id:
            # Force the next _coordinator_tick to treat the coordinator as
            # missing immediately rather than waiting out the full timeout
            # window -- a clean TCP disconnect is a stronger, faster signal
            # than heartbeat silence. Bounded by one heartbeat interval of
            # latency (the next tick), not immediate, to avoid adding a
            # second cross-thread wakeup path just for this.
            self._last_coordinator_heartbeat_at = None

    async def _trigger_peer_connected_handler(self) -> None:
        await trio.sleep(_MESH_FORM_DELAY_SECONDS)
        handler = self._on_peer_connected_handler
        loop = self._loop
        if handler is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(handler(), loop)

    async def list_peers(self) -> list[PeerInfo]:
        return [
            PeerInfo(peer_id=pid, address=addr, connected=True)
            for pid, addr in self._connected_peers.items()
        ]

    def peer_is_lan(self, peer_id: str) -> bool:
        """Used by sync/apply.py to decide which of sync.eager_files_lan/
        _wan (issue #123) governs a given incoming file. An unconnected/
        unknown peer_id (empty address) classifies as WAN — see
        _is_lan_address."""
        return _is_lan_address(self._connected_peers.get(peer_id, ""))

    async def list_peer_capabilities(self) -> dict[str, list[str]]:
        return dict(self._peer_capabilities)

    async def publish_capabilities(self, capabilities: list[str]) -> None:
        """Broadcast this node's own capability tags to the mesh. Unlike
        publish_state_change, there's no vector-clock bookkeeping here --
        this isn't conflict-tracked content, just "latest broadcast wins"
        (see _receive_loop's node_capabilities branch)."""
        if self._thread is None:
            return
        envelope = json.dumps(
            {
                "entity_type": "node_capabilities",
                "entity_id": self.node_id,
                "vector_clock": {},
                "origin_node_id": self.node_id,
                "payload": {"capabilities": capabilities},
            }
        ).encode()
        await self._call_trio(self._trio_publish, envelope)

    async def get_coordinator_status(self) -> CoordinatorStatus:
        """Direct attribute read, no _call_trio round-trip -- same
        best-effort GIL-protected-dict-read convention list_peers() and
        list_peer_capabilities() already use for state written from the
        trio thread and read from the asyncio side."""
        return CoordinatorStatus(
            coordinator_id=self._coordinator_id,
            term=self._coordinator_term,
            is_self=self._coordinator_id == self._node_id,
            self_score=self._self_score,
            peer_scores=dict(self._peer_scores),
        )

    async def reclaim_coordinator(self) -> None:
        """Human-triggered failback override (#101's explicit non-default
        escape hatch -- election never auto-fails-back once a healthy
        replacement takes over, see _coordinator_tick). Bumping the term
        unconditionally wins over any existing claim regardless of score:
        every peer's _handle_coordinator_heartbeat adopts on `remote_term >
        self._coordinator_term` before it ever compares scores, so this
        node's next heartbeat is accepted mesh-wide without needing to
        actually be the highest-scored peer -- a human asked for it
        explicitly, which is a stronger signal than the score heuristic."""
        if self._thread is None:
            return
        await self._call_trio(self._trio_reclaim_coordinator)

    async def _trio_reclaim_coordinator(self) -> None:
        self._coordinator_term += 1
        self._coordinator_id = self._node_id
        self._last_coordinator_heartbeat_at = trio.current_time()
        await self._publish_coordinator_heartbeat()

    async def _coordinator_loop(self) -> None:
        while True:
            try:
                await self._coordinator_tick()
            except Exception:
                logger.exception("Coordinator election tick failed")
            await trio.sleep(self._coordinator_heartbeat_interval)

    async def _coordinator_tick(self) -> None:
        assert self._started_monotonic is not None
        now = trio.current_time()
        self._self_score = compute_capability_score(self._sync_dir, now - self._started_monotonic)

        if self._coordinator_id == self._node_id:
            self._last_coordinator_heartbeat_at = now

        timed_out = (
            self._coordinator_id is not None
            and self._coordinator_id != self._node_id
            and (
                self._last_coordinator_heartbeat_at is None
                or now - self._last_coordinator_heartbeat_at > self._coordinator_timeout
            )
        )
        if self._coordinator_id is None or timed_out:
            best_id, best_score = self._node_id, self._self_score
            assert best_id is not None
            for peer_id, score in self._peer_scores.items():
                if peer_id not in self._connected_peers:
                    continue  # a stale score from a disconnected peer isn't a valid candidate
                if outranks(peer_id, score, best_id, best_score):
                    best_id, best_score = peer_id, score
            if best_id == self._node_id:
                if timed_out:
                    logger.info(
                        "Coordinator %s unresponsive (term=%s) -- electing self "
                        "(term=%s, score=%.2f)",
                        self._coordinator_id,
                        self._coordinator_term,
                        self._coordinator_term + 1,
                        self._self_score,
                    )
                self._coordinator_term += 1
                self._coordinator_id = self._node_id
                self._last_coordinator_heartbeat_at = now
            # else: the higher-scored peer is known but hasn't self-claimed
            # yet -- wait for their own heartbeat rather than claiming on
            # their behalf. If they never do (gone stale), _on_peer_
            # disconnected/staleness naturally drops them as a candidate on
            # a later tick.

        await self._publish_coordinator_heartbeat()

    async def _publish_coordinator_heartbeat(self) -> None:
        envelope = json.dumps(
            {
                "entity_type": "coordinator_heartbeat",
                "entity_id": "coordinator",
                "vector_clock": {},
                "origin_node_id": self._node_id,
                "payload": {
                    "term": self._coordinator_term,
                    "is_coordinator": self._coordinator_id == self._node_id,
                    "score": self._self_score,
                },
            }
        ).encode()
        await self._trio_publish(envelope)

    def _handle_coordinator_heartbeat(self, origin_node_id: str, payload: dict[str, Any]) -> None:
        """Every peer broadcasts every tick (carrying its own score, used
        to build _peer_scores for candidate ranking); only a self-claim
        (is_coordinator=True, origin_node_id asserting itself, never a
        third party relaying belief about someone else) is authoritative
        for _coordinator_id/_coordinator_term -- classic bully-election
        semantics. Higher term always wins outright (the split-brain
        defense: a coordinator that sees a higher term steps down
        immediately, engine.py:_coordinator_tick's own election bumps the
        term specifically so its claim wins over any stale lower-term
        claim still circulating). Equal term with a different claimed
        coordinator is a genuine simultaneous-election collision (e.g. two
        peers each self-electing on their very first tick before they'd
        heard from each other) -- resolved by the same outranks() total
        order used for candidate selection, so every peer converges on the
        identical winner from the same gossiped inputs."""
        try:
            remote_term = int(payload["term"])
            is_coordinator = bool(payload["is_coordinator"])
            score = float(payload["score"])
        except (KeyError, TypeError, ValueError):
            return
        self._peer_scores[origin_node_id] = score
        if not is_coordinator:
            return

        current_id = self._coordinator_id
        if current_id is None:
            current_score = float("-inf")
        elif current_id == self._node_id:
            current_score = self._self_score
        else:
            current_score = self._peer_scores.get(current_id, float("-inf"))

        if (
            current_id is None
            or remote_term > self._coordinator_term
            or (
                remote_term == self._coordinator_term
                and origin_node_id != current_id
                and outranks(origin_node_id, score, current_id, current_score)
            )
        ):
            if current_id == self._node_id and origin_node_id != self._node_id:
                logger.info(
                    "Stepping down as coordinator: %s has priority "
                    "(term=%s, score=%.2f vs my %.2f)",
                    origin_node_id,
                    remote_term,
                    score,
                    self._self_score,
                )
            self._coordinator_term = remote_term
            self._coordinator_id = origin_node_id
            self._last_coordinator_heartbeat_at = trio.current_time()
        elif origin_node_id == current_id:
            self._last_coordinator_heartbeat_at = trio.current_time()

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

    async def _handle_agent_dispatch_stream(self, stream: INetStream) -> None:
        """Server side of agent_dispatch.py's wire protocol. Acks
        immediately (accepted iff a handler is registered — see that
        module's docstring for why deeper validation isn't done here) and
        hands the actual work to asyncio fire-and-forget, the same
        cross-thread pattern _receive_loop uses for incoming gossipsub
        messages."""
        try:
            request = AgentDispatchRequest.from_bytes(await read_framed(stream))
            handler = self._on_agent_dispatch
            loop = self._loop
            accepted = handler is not None and loop is not None
            reason = None if accepted else "no dispatch handler registered"
            await write_framed(
                stream, json.dumps({"accepted": accepted, "reason": reason}).encode()
            )
            if accepted:
                assert handler is not None and loop is not None
                asyncio.run_coroutine_threadsafe(handler(request), loop)
        except Exception:
            logger.warning("Error handling agent-dispatch stream request", exc_info=True)
        finally:
            await stream.close()

    async def dispatch_agent_remotely(self, peer_id: str, request: AgentDispatchRequest) -> bool:
        """Client side: ask peer_id to run the agent described by request.
        Returns True only if the peer acked acceptance -- False on a clean
        reject, timeout, or any transport failure (callers can't
        distinguish those, same convention as request_file)."""
        try:
            return await self._call_trio(self._trio_dispatch_agent, peer_id, request)
        except Exception:
            logger.warning("Could not dispatch agent remotely to peer %s", peer_id, exc_info=True)
            return False

    async def _trio_dispatch_agent(self, peer_id: str, request: AgentDispatchRequest) -> bool:
        assert self._host is not None
        stream = await self._host.new_stream(ID.from_base58(peer_id), [AGENT_DISPATCH_PROTOCOL])
        try:
            with trio.fail_after(_AGENT_DISPATCH_ACK_TIMEOUT_SECONDS):
                await write_framed(stream, request.to_bytes())
                await stream.close_write()  # pyright: ignore[reportAttributeAccessIssue]
                response = json.loads((await read_framed(stream)).decode())
                return bool(response.get("accepted", False))
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
