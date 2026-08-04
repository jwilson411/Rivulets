"""Workspace-scoped LAN peer discovery via mDNS (FR-9.3).

py-libp2p 0.7.0 ships a built-in `libp2p.discovery.mdns.mdns.MDNSDiscovery`,
but reading its source turned up two bugs that rule it out here, confirmed
by running it: its zeroconf service type is a hardcoded module constant
(`SERVICE_TYPE = "_p2p._udp.local."`, no way to override it), so
`enable_mDNS=True` on `new_host()` gives every py-libp2p peer on the LAN
visibility into every other one — no workspace scoping at all. Worse, it
always advertises the fixed default port (8000) instead of the host's
real bound port, so even a same-workspace peer's discovered address isn't
dialable.

The lower-level `PeerBroadcaster`/`PeerListener` classes underneath it
don't have either problem — both take an explicit `service_type`, and the
broadcaster takes an explicit `port`. This module drives those directly
with a workspace-derived service type and the host's actual bound port,
so only nodes sharing the same workspace ID discover each other, and the
addresses they discover are dialable.

DNS-SD (RFC 6763) caps the service label at 15 bytes, so the
workspace-derived tag is truncated to 8 hex characters of a SHA-256 hash
(`_p2phv-{8 hex chars}._udp.local.` — 15 bytes exactly). A full-length
hash raises `zeroconf.BadTypeInNameException`.
"""

import hashlib
from collections.abc import Callable

from libp2p.abc import IHost
from libp2p.discovery.events.peerDiscovery import peerDiscovery
from libp2p.discovery.mdns.broadcaster import PeerBroadcaster
from libp2p.discovery.mdns.listener import PeerListener
from libp2p.peer.peerinfo import PeerInfo
from zeroconf import Zeroconf


def service_type_for_workspace(workspace_fingerprint: str) -> str:
    tag = hashlib.sha256(workspace_fingerprint.encode()).hexdigest()[:8]
    return f"_p2phv-{tag}._udp.local."


class WorkspaceMDNS:
    """Advertises `host` under a workspace-scoped mDNS service and browses
    for peers advertising the same one. Must be started/stopped from
    inside the host's own `async with host.run(...)` block, since it needs
    the host's real bound port."""

    def __init__(self, host: IHost, workspace_fingerprint: str, bound_port: int) -> None:
        self._zeroconf = Zeroconf()
        service_type = service_type_for_workspace(workspace_fingerprint)
        node_id = str(host.get_id())
        # service_name just needs to be unique per node under this
        # service_type — PeerListener uses it only to skip re-discovering
        # its own broadcast (listener.py: `if name == self.service_name`).
        service_name = f"{node_id}.{service_type}"
        self._broadcaster = PeerBroadcaster(
            zeroconf=self._zeroconf,
            service_type=service_type,
            service_name=service_name,
            peer_id=node_id,
            port=bound_port,
        )
        self._listener = PeerListener(
            peerstore=host.get_peerstore(),
            zeroconf=self._zeroconf,
            service_type=service_type,
            service_name=service_name,
        )

    def start(self) -> None:
        self._broadcaster.register()

    def stop(self) -> None:
        self._broadcaster.unregister()
        self._listener.stop()
        self._zeroconf.close()


def on_peer_discovered(handler: Callable[[PeerInfo], None]) -> None:
    """Registers a callback fired (synchronously, from zeroconf's own
    thread) whenever any WorkspaceMDNS instance in this process discovers
    a peer. This is a process-wide singleton in py-libp2p itself
    (libp2p.discovery.events.peerDiscovery) — fine here since Agent Hive
    only ever runs one SyncEngine (and therefore one WorkspaceMDNS) per
    process (single-workspace-per-node, data-model.md)."""
    peerDiscovery.register_peer_discovered_handler(handler)
