"""Per-installation libp2p node identity (FR-9.3's peer IDs, ADR-006).

This is deliberately independent from the workspace key: the workspace key
identifies the *workspace* (and gates who can join its sync mesh via PNet
— see engine.py), while this module generates a stable per-node Ed25519
identity so each installation has its own libp2p peer ID across restarts.
Two nodes sharing a workspace key must NOT derive the same peer ID (that
would make them indistinguishable to gossipsub/vector-clock bookkeeping),
so this can't be derived from the workspace key — it's a random seed,
generated once and persisted locally, never synced (same exclusion
principle as FR-9.2's credential handling, extended to node identity).
"""

import os
from pathlib import Path

from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.crypto.keys import KeyPair

_SEED_BYTES = 32


def load_or_create_node_key(sync_dir: Path) -> KeyPair:
    sync_dir.mkdir(parents=True, exist_ok=True)
    seed_path = sync_dir / "node_key"
    if seed_path.exists():
        seed = seed_path.read_bytes()
        if len(seed) != _SEED_BYTES:
            raise RuntimeError(f"Corrupt node key at {seed_path}: expected 32 bytes")
    else:
        seed = os.urandom(_SEED_BYTES)
        seed_path.write_bytes(seed)
        seed_path.chmod(0o600)
    return create_new_key_pair(seed)
