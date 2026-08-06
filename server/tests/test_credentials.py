"""Provider key storage (security/credentials.py, NFR-3.3).

No prior test file exercised this module directly (other tests only hit
store_provider_key's success path indirectly through api/providers.py,
via conftest.py's in-memory keyring backend, which never fails). Failure
paths are exercised here by monkeypatching the `keyring` module's
functions directly to raise, since the in-memory backend always succeeds.
"""

import keyring.errors
import pytest

from rivulets.security.credentials import (
    CredentialStoreError,
    delete_provider_key,
    get_provider_key,
    store_provider_key,
)


def test_store_provider_key_returns_ref_and_round_trips() -> None:
    ref = store_provider_key("provider-1", "sk-real-key")
    assert ref == "provider-key:provider-1"
    assert get_provider_key(ref) == "sk-real-key"


def test_store_provider_key_wraps_keyring_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr("keyring.set_password", _boom)

    with pytest.raises(CredentialStoreError, match="No OS keychain backend available"):
        store_provider_key("provider-2", "sk-x")


def test_get_provider_key_wraps_keyring_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.KeyringError("backend unavailable")

    monkeypatch.setattr("keyring.get_password", _boom)

    with pytest.raises(CredentialStoreError, match="Could not read provider key"):
        get_provider_key("provider-key:missing")


def test_get_provider_key_raises_when_nothing_stored() -> None:
    with pytest.raises(CredentialStoreError, match="No credential stored for ref"):
        get_provider_key("provider-key:never-stored")


def test_delete_provider_key_is_idempotent_when_already_gone() -> None:
    ref = store_provider_key("provider-3", "sk-y")
    delete_provider_key(ref)
    delete_provider_key(ref)  # second delete must not raise


def test_delete_provider_key_wraps_keyring_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.KeyringError("backend unavailable")

    monkeypatch.setattr("keyring.delete_password", _boom)

    with pytest.raises(CredentialStoreError, match="Could not delete provider key"):
        delete_provider_key("provider-key:provider-3")
