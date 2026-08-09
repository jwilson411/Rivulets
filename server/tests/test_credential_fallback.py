"""Encrypted-SQLite provider-key fallback (security/credential_fallback.py,
#118). Exercised directly here at the storage layer -- security/
credentials.py's own tests cover the routing decision (keyring vs.
fallback); this file covers the fallback's own encrypt/store/decrypt
round trip and its file isolation from the synced app database.
"""

from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from rivulets.security import credential_fallback

_KEY_A = b"\x01" * 32
_KEY_B = b"\x02" * 32


def test_store_and_get_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "credentials.db"
    credential_fallback.store(db_path, "provider-key:1", "sk-secret", _KEY_A)
    assert credential_fallback.get(db_path, "provider-key:1", _KEY_A) == "sk-secret"


def test_get_returns_none_when_nothing_stored(tmp_path: Path) -> None:
    db_path = tmp_path / "credentials.db"
    assert credential_fallback.get(db_path, "provider-key:missing", _KEY_A) is None


def test_store_overwrites_existing_ref(tmp_path: Path) -> None:
    db_path = tmp_path / "credentials.db"
    credential_fallback.store(db_path, "provider-key:1", "sk-old", _KEY_A)
    credential_fallback.store(db_path, "provider-key:1", "sk-new", _KEY_A)
    assert credential_fallback.get(db_path, "provider-key:1", _KEY_A) == "sk-new"


def test_delete_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "credentials.db"
    credential_fallback.store(db_path, "provider-key:1", "sk-secret", _KEY_A)
    credential_fallback.delete(db_path, "provider-key:1")
    credential_fallback.delete(db_path, "provider-key:1")  # must not raise
    assert credential_fallback.get(db_path, "provider-key:1", _KEY_A) is None


def test_wrong_key_fails_to_decrypt(tmp_path: Path) -> None:
    """AES-GCM authenticates the ciphertext -- a wrong key must raise
    rather than silently return garbage, since a corrupted decrypt here
    would otherwise surface as a bogus API key sent to a provider."""
    db_path = tmp_path / "credentials.db"
    credential_fallback.store(db_path, "provider-key:1", "sk-secret", _KEY_A)
    with pytest.raises(InvalidTag):
        credential_fallback.get(db_path, "provider-key:1", _KEY_B)


def test_the_stored_file_is_not_the_app_database(tmp_path: Path) -> None:
    """#118's design decision: provider keys must never enter the synced
    rivulets.db, so the fallback gets its own file (config.py's
    credential_fallback_db_path)."""
    db_path = tmp_path / "credentials.db"
    app_db_path = tmp_path / "rivulets.db"
    credential_fallback.store(db_path, "provider-key:1", "sk-secret", _KEY_A)
    assert db_path.exists()
    assert not app_db_path.exists()
