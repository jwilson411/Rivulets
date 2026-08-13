"""Encrypted-SQLite fallback for provider key storage (#118).

Used only when no OS keychain backend is available (headless CI, Docker,
some Linux setups without a Secret Service provider) — see
security/credentials.py, which decides when to reach for this module and
never uses it as the primary store.

Design decision (issue #118, agreed 2026-08-09): the encryption key is
HKDF-derived from the workspace key itself (security/keys.py's
derive_credential_store_key), not a second passphrase the user has to
separately manage — this reuses the trust model already in place for P2P
sync, where the workspace key also serves as the libp2p pre-shared key.

Accepted tradeoff: this raises the blast radius of a workspace-key/
mnemonic compromise to include provider API keys, not just sync access.
That's a deliberate choice, not an oversight — but it means callers that
surface the mnemonic to the user (or that know this fallback is active)
must disclose it; see api/providers.py's `/providers/credential-storage`
endpoint and the UI banner it drives.

Storage lives in its own file (config.py's credential_fallback_db_path),
never in the synced `rivulets.db` — provider keys must never enter a sync
payload (NFR-3.3), and this file is deliberately outside that database so
there's no path by which they could.
"""

import os
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12  # 96 bits, AES-GCM's recommended nonce size


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_file = not db_path.exists()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS provider_keys ("
        "ref TEXT PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL)"
    )
    if is_new_file:
        # Encrypted or not, this file holds provider-key ciphertext -- match
        # sync/identity.py's node_key handling rather than relying solely on
        # main.py's process umask, since callers (tests, scripts) can reach
        # this module directly without going through that entry point.
        db_path.chmod(0o600)
    return conn


def store(db_path: Path, ref: str, api_key: str, encryption_key: bytes) -> None:
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(encryption_key).encrypt(nonce, api_key.encode("utf-8"), None)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO provider_keys (ref, nonce, ciphertext) VALUES (?, ?, ?) "
                "ON CONFLICT(ref) DO UPDATE SET nonce = excluded.nonce, "
                "ciphertext = excluded.ciphertext",
                (ref, nonce, ciphertext),
            )
    finally:
        conn.close()


def get(db_path: Path, ref: str, encryption_key: bytes) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT nonce, ciphertext FROM provider_keys WHERE ref = ?", (ref,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    nonce, ciphertext = row
    return AESGCM(encryption_key).decrypt(nonce, ciphertext, None).decode("utf-8")


def delete(db_path: Path, ref: str) -> None:
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM provider_keys WHERE ref = ?", (ref,))
    finally:
        conn.close()
