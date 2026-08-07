"""SessionKeyStore (security/session.py) -- the "no active session" guard
that api/deps.py relies on for its 401 path."""

import pytest

from rivulets.security.session import SessionKeyStore


def test_get_key_raises_when_no_session_is_active() -> None:
    store = SessionKeyStore()
    with pytest.raises(RuntimeError, match="No active session"):
        store.get_key()


def test_get_p2p_psk_raises_when_no_session_is_active() -> None:
    store = SessionKeyStore()
    with pytest.raises(RuntimeError, match="No active session"):
        store.get_p2p_psk()


def test_set_and_clear_round_trip() -> None:
    store = SessionKeyStore()
    store.set_key(b"jwt-key")
    store.set_p2p_psk(b"psk")
    assert store.get_key() == b"jwt-key"
    assert store.get_p2p_psk() == b"psk"

    store.clear()
    with pytest.raises(RuntimeError):
        store.get_key()
    with pytest.raises(RuntimeError):
        store.get_p2p_psk()
