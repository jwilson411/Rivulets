"""In-memory holder for the current session's derived JWT signing key.

Agent Hive is single-workspace, single-user per node (data-model.md: "exactly
one row per installation"), so one active signing key at a time is enough.
The key is set on successful login and never written to disk — it's
re-derivable from the mnemonic, which is the only thing the user persists
themselves (per the BIP-39 recovery design, OQ-7).
"""

import threading


class SessionKeyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jwt_signing_key: bytes | None = None

    def set_key(self, key: bytes) -> None:
        with self._lock:
            self._jwt_signing_key = key

    def clear(self) -> None:
        with self._lock:
            self._jwt_signing_key = None

    def get_key(self) -> bytes:
        with self._lock:
            if self._jwt_signing_key is None:
                raise RuntimeError("No active session — login required")
            return self._jwt_signing_key


_store = SessionKeyStore()


def get_session_key_store() -> SessionKeyStore:
    return _store
