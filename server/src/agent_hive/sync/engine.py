"""P2P sync engine interface (ADR-006, FR-9).

Real implementation transports structured-state changes over libp2p
gossipsub and files over content-addressed delta transfer, with
Tailscale/WireGuard handling cross-network NAT traversal. The Python
libp2p ecosystem is still immature enough that pinning a binding is a
decision worth making deliberately rather than as a side effect of
scaffolding — so this module defines the shape the rest of the app
(App Server routes, vector-clock conflict resolution) codes against,
with every network operation stubbed.

Wire-up TODO: pick a libp2p binding (or a narrower alternative — see
docs/architecture/adrs.md ADR-006 "Alternatives considered"), implement
connect/publish/subscribe against it, and replace the NotImplementedError
bodies below without changing this interface.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PeerInfo:
    peer_id: str
    address: str
    connected: bool


class SyncEngine:
    def __init__(self, workspace_key: bytes) -> None:
        self._workspace_key = workspace_key  # used as the noise handshake PSK

    async def start(self) -> None:
        """Begin mDNS LAN discovery and listen for incoming peer connections."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def connect(self, address: str) -> PeerInfo:
        """Manually connect to a peer by multiaddr (FR-9.3 fallback path)."""
        raise NotImplementedError

    async def disconnect(self, peer_id: str) -> None:
        raise NotImplementedError

    async def list_peers(self) -> list[PeerInfo]:
        raise NotImplementedError

    async def publish_state_change(
        self, entity_type: str, entity_id: str, payload: dict[str, object], vector_clock: int
    ) -> None:
        """Publish an entity change to the `workspace/state` gossipsub topic."""
        raise NotImplementedError

    async def request_file(self, content_hash: str) -> None:
        """Request file bytes from a peer whose hash matches; delta transfer
        only fetches content when the local hash differs (FR-9.7)."""
        raise NotImplementedError
