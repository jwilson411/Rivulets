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
derived at login. The credential-store key (#118,
security/credential_fallback.py) is set here for the same reason too,
even though most nodes never end up using it — it's cheap to derive
alongside the others, and only actually gets read when the OS keychain
has no usable backend. The webhook-secret key (#99) is set here for the
same reason again — it's only read when an inbound webhook POST actually
arrives, which is exactly why api/webhooks.py's trigger endpoint surfaces
a clear "not unlocked on this node" 401 rather than a generic 500 when
this key isn't set yet, mirroring accept_invite's identical situation.
"""

import threading


class SessionKeyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jwt_signing_key: bytes | None = None
        self._p2p_psk: bytes | None = None
        self._credential_store_key: bytes | None = None
        self._webhook_secret_key: bytes | None = None

    def set_key(self, key: bytes) -> None:
        with self._lock:
            self._jwt_signing_key = key

    def set_p2p_psk(self, psk: bytes) -> None:
        with self._lock:
            self._p2p_psk = psk

    def set_credential_store_key(self, key: bytes) -> None:
        with self._lock:
            self._credential_store_key = key

    def set_webhook_secret_key(self, key: bytes) -> None:
        with self._lock:
            self._webhook_secret_key = key

    def clear(self) -> None:
        with self._lock:
            self._jwt_signing_key = None
            self._p2p_psk = None
            self._credential_store_key = None
            self._webhook_secret_key = None

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

    def get_credential_store_key(self) -> bytes:
        with self._lock:
            if self._credential_store_key is None:
                raise RuntimeError("No active session — login required")
            return self._credential_store_key

    def get_webhook_secret_key(self) -> bytes:
        with self._lock:
            if self._webhook_secret_key is None:
                raise RuntimeError("No active session — login required")
            return self._webhook_secret_key


_store = SessionKeyStore()


def get_session_key_store() -> SessionKeyStore:
    return _store
