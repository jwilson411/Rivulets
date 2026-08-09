"""LLM provider key storage: OS keychain via `keyring`, preferred; an
encrypted-SQLite store (credential_fallback.py) as a deliberate fallback.

`provider_config.api_key_ref` stores only the reference name below — the
raw key never touches the app database (`rivulets.db`) or a sync payload,
regardless of which backend below actually holds it.

If no OS keychain backend is available (headless CI, Docker, some Linux
setups without a Secret Service provider), `keyring.set_password` et al.
raise `keyring.errors.KeyringError` (see `is_keyring_available` below —
keyring resolves to its own `backends.fail.Keyring` in that case, which
raises on every operation). Rather than surfacing that as a hard error
(#118's original behavior — provider keys simply couldn't be added at all
under Docker), operations here fall back to credential_fallback.py,
encrypted with a key derived from the workspace mnemonic rather than a
second passphrase (security/keys.py's derive_credential_store_key). The
OS keychain remains strictly preferred: this fallback only ever engages
when keyring itself reports no usable backend.
"""

import keyring
import keyring.errors

from rivulets.config import get_settings
from rivulets.security import credential_fallback
from rivulets.security.session import get_session_key_store

_SERVICE_NAME = "rivulets"


class CredentialStoreError(RuntimeError):
    pass


def _ref_for(provider_config_id: str) -> str:
    return f"provider-key:{provider_config_id}"


def is_keyring_available() -> bool:
    """True if `keyring` resolved to a real OS backend rather than its
    built-in `backends.fail.Keyring` (installed when no Secret Service/
    macOS Keychain/Windows Credential Locker is present). Used to decide
    which backend is authoritative — both for routing store/get/delete
    below and for `api/providers.py`'s `/credential-storage` status the
    UI uses to disclose the fallback per #118's agreed design."""
    return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)


def store_provider_key(provider_config_id: str, api_key: str) -> str:
    """Store the key in the OS keychain (or the encrypted-SQLite fallback
    if no keychain backend is available) and return the reference to
    persist as `provider_config.api_key_ref`."""
    ref = _ref_for(provider_config_id)
    try:
        keyring.set_password(_SERVICE_NAME, ref, api_key)
    except keyring.errors.KeyringError:
        credential_fallback.store(
            get_settings().credential_fallback_db_path,
            ref,
            api_key,
            get_session_key_store().get_credential_store_key(),
        )
    return ref


def get_provider_key(api_key_ref: str) -> str:
    try:
        secret = keyring.get_password(_SERVICE_NAME, api_key_ref)
    except keyring.errors.KeyringError:
        secret = credential_fallback.get(
            get_settings().credential_fallback_db_path,
            api_key_ref,
            get_session_key_store().get_credential_store_key(),
        )
    if secret is None:
        raise CredentialStoreError(f"No credential stored for ref '{api_key_ref}'.")
    return secret


def delete_provider_key(api_key_ref: str) -> None:
    try:
        keyring.delete_password(_SERVICE_NAME, api_key_ref)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone — deletion is idempotent
    except keyring.errors.KeyringError:
        credential_fallback.delete(get_settings().credential_fallback_db_path, api_key_ref)
