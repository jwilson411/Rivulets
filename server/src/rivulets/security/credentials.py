"""Secret storage: OS keychain via `keyring`, preferred; an encrypted-
SQLite store (credential_fallback.py) as a deliberate fallback.

Originally provider-key-only (#118); generalized to `store_secret`/
`get_secret`/`delete_secret` for #124 (MCP server auth headers), which
need the same never-touches-the-app-database guarantee for a different
kind of secret. `store_provider_key`/`get_provider_key`/
`delete_provider_key` remain as the provider-key-specific callers
actually use, now thin wrappers with the `provider-key:` ref prefix.

`provider_config.api_key_ref` stores only the reference name below — the
raw key never touches the app database (`rivulets.db`) or a sync payload,
regardless of which backend below actually holds it. Same guarantee holds
for any other caller's ref (e.g. `mcp_server.header_names_json` only ever
stores header *names*, never values).

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
import keyring.backends.fail
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


def store_secret(ref: str, value: str) -> None:
    """Store `value` in the OS keychain (or the encrypted-SQLite fallback
    if no keychain backend is available) under `ref`. Callers own the ref
    format/uniqueness — this module just persists it."""
    try:
        keyring.set_password(_SERVICE_NAME, ref, value)
    except keyring.errors.KeyringError:
        credential_fallback.store(
            get_settings().credential_fallback_db_path,
            ref,
            value,
            get_session_key_store().get_credential_store_key(),
        )


def get_secret(ref: str) -> str:
    try:
        secret = keyring.get_password(_SERVICE_NAME, ref)
    except keyring.errors.KeyringError:
        secret = credential_fallback.get(
            get_settings().credential_fallback_db_path,
            ref,
            get_session_key_store().get_credential_store_key(),
        )
    if secret is None:
        raise CredentialStoreError(f"No credential stored for ref '{ref}'.")
    return secret


def delete_secret(ref: str) -> None:
    try:
        keyring.delete_password(_SERVICE_NAME, ref)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone — deletion is idempotent
    except keyring.errors.KeyringError:
        credential_fallback.delete(get_settings().credential_fallback_db_path, ref)


def store_provider_key(provider_config_id: str, api_key: str) -> str:
    """Store the key and return the reference to persist as
    `provider_config.api_key_ref`."""
    ref = _ref_for(provider_config_id)
    store_secret(ref, api_key)
    return ref


def get_provider_key(api_key_ref: str) -> str:
    return get_secret(api_key_ref)


def delete_provider_key(api_key_ref: str) -> None:
    delete_secret(api_key_ref)
