"""Provider key storage (security/credentials.py, NFR-3.3, #118).

No prior test file exercised this module directly (other tests only hit
store_provider_key's success path indirectly through api/providers.py,
via conftest.py's in-memory keyring backend, which never fails). Failure
paths are exercised here by monkeypatching the `keyring` module's
functions directly to raise, since the in-memory backend always succeeds.

Since #118, a raised `keyring.errors.KeyringError` no longer hard-fails
the operation — it falls back to credential_fallback.py, encrypted with
the session's credential-store key (security/session.py). That key is
only ever set at login (api/auth.py), so these tests set it directly via
get_session_key_store() to exercise the fallback path in isolation,
without going through a full login.
"""

import keyring.errors
import pytest

from rivulets.security.credentials import (
    CredentialStoreError,
    delete_provider_key,
    get_provider_key,
    store_provider_key,
)
from rivulets.security.session import get_session_key_store


@pytest.fixture(autouse=True)
def _credential_store_key() -> None:
    """Every test in this file may hit the fallback path (keyring failures
    are monkeypatched per-test below), so a session key is always present
    — mirroring login() always deriving one regardless of whether a given
    node ends up needing it (session.py's module docstring)."""
    get_session_key_store().set_credential_store_key(b"\x00" * 32)
    yield
    get_session_key_store().clear()


def test_store_provider_key_returns_ref_and_round_trips() -> None:
    ref = store_provider_key("provider-1", "sk-real-key")
    assert ref == "provider-key:provider-1"
    assert get_provider_key(ref) == "sk-real-key"


def test_store_and_get_fall_back_when_no_keyring_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr("keyring.set_password", _boom)
    monkeypatch.setattr("keyring.get_password", _boom)

    ref = store_provider_key("provider-2", "sk-x")
    assert ref == "provider-key:provider-2"
    assert get_provider_key(ref) == "sk-x"


def test_get_provider_key_raises_when_nothing_stored() -> None:
    with pytest.raises(CredentialStoreError, match="No credential stored for ref"):
        get_provider_key("provider-key:never-stored")


def test_get_provider_key_raises_when_nothing_stored_in_fallback_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.KeyringError("backend unavailable")

    monkeypatch.setattr("keyring.get_password", _boom)

    with pytest.raises(CredentialStoreError, match="No credential stored for ref"):
        get_provider_key("provider-key:missing")


def test_delete_provider_key_is_idempotent_when_already_gone() -> None:
    ref = store_provider_key("provider-3", "sk-y")
    delete_provider_key(ref)
    delete_provider_key(ref)  # second delete must not raise


def test_delete_provider_key_falls_back_when_no_keyring_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.KeyringError("backend unavailable")

    monkeypatch.setattr("keyring.set_password", _boom)
    monkeypatch.setattr("keyring.delete_password", _boom)
    monkeypatch.setattr("keyring.get_password", _boom)

    ref = store_provider_key("provider-4", "sk-z")
    delete_provider_key(ref)  # must not raise
    with pytest.raises(CredentialStoreError, match="No credential stored for ref"):
        get_provider_key(ref)
