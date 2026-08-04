"""LLM provider key storage (NFR-3.3): OS keychain via `keyring`, never SQLite.

`provider_config.api_key_ref` stores only the reference name below — the
raw key never touches the database or a sync payload (FR-1.5).

If no OS keychain backend is available (headless CI, some Linux setups
without a Secret Service provider), operations raise `CredentialStoreError`
rather than silently falling back to an on-disk store. docs/infrastructure/
security-and-dr.md documents an encrypted-SQLite fallback for that case;
it's intentionally not implemented here yet — swallowing this error into a
weaker storage mode is a decision to make deliberately, not by accident.
"""

import keyring
import keyring.errors

_SERVICE_NAME = "agent-hive"


class CredentialStoreError(RuntimeError):
    pass


def _ref_for(provider_config_id: str) -> str:
    return f"provider-key:{provider_config_id}"


def store_provider_key(provider_config_id: str, api_key: str) -> str:
    """Store the key in the OS keychain and return the reference to persist
    as `provider_config.api_key_ref`."""
    ref = _ref_for(provider_config_id)
    try:
        keyring.set_password(_SERVICE_NAME, ref, api_key)
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreError(
            "No OS keychain backend available to store this provider key."
        ) from exc
    return ref


def get_provider_key(api_key_ref: str) -> str:
    try:
        secret = keyring.get_password(_SERVICE_NAME, api_key_ref)
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreError("Could not read provider key from keychain.") from exc
    if secret is None:
        raise CredentialStoreError(f"No credential stored for ref '{api_key_ref}'.")
    return secret


def delete_provider_key(api_key_ref: str) -> None:
    try:
        keyring.delete_password(_SERVICE_NAME, api_key_ref)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone — deletion is idempotent
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreError("Could not delete provider key from keychain.") from exc
