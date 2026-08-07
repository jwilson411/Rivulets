"""In-memory holder for the current session's derived keys.

Rivulets is single-workspace per node (data-model.md: "exactly one row per
installation"), so one active set of keys at a time is enough — even with
multiple humans sharing that workspace (#14/#15), they all authenticate
against the same mnemonic-derived signing key, just with different
`human_id`/`grant` claims layered on top of it (see api/deps.py's
SessionClaims). Every key here is set on successful login and never
written to disk — all of them are re-derivable from the mnemonic, which is
the only thing the user persists themselves (per the BIP-39 recovery
design, OQ-7). The p2p PSK (FR-9.4, see sync/engine.py's module docstring
for what it actually gates) lives here alongside the JWT signing key for
the same reason: it's only available once the workspace key has been
derived at login.
"""

import threading


class SessionKeyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jwt_signing_key: bytes | None = None
        self._p2p_psk: bytes | None = None

    def set_key(self, key: bytes) -> None:
        with self._lock:
            self._jwt_signing_key = key

    def set_p2p_psk(self, psk: bytes) -> None:
        with self._lock:
            self._p2p_psk = psk

    def clear(self) -> None:
        with self._lock:
            self._jwt_signing_key = None
            self._p2p_psk = None

    def get_key(self) -> bytes:
        with self._lock:
            if self._jwt_signing_key is None:
                raise RuntimeError("No active session — login required")
            return self._jwt_signing_key

    def get_p2p_psk(self) -> bytes:
        with self._lock:
            if self._p2p_psk is None:
                raise RuntimeError("No active session — login required")
            return self._p2p_psk


_store = SessionKeyStore()


def get_session_key_store() -> SessionKeyStore:
    return _store
