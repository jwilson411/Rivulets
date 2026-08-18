"""Token JSON in the credential store. Refs only — never the raw secret."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rivulets.db.base import utcnow_iso
from rivulets.integrations.jsonutil import as_dict, as_str, string_list
from rivulets.security.credentials import (
    CredentialStoreError,
    delete_secret,
    get_secret,
    store_secret,
)

_OAUTH_APP_SECRET_PREFIX = "integration-oauth:"  # noqa: S105
_ACCOUNT_TOKEN_PREFIX = "integration:"  # noqa: S105


def oauth_app_secret_ref(provider: str) -> str:
    return f"{_OAUTH_APP_SECRET_PREFIX}{provider}"


def account_token_ref(account_id: str) -> str:
    return f"{_ACCOUNT_TOKEN_PREFIX}{account_id}"


@dataclass(frozen=True, slots=True)
class StoredTokens:
    access_token: str
    refresh_token: str | None
    expiry: datetime | None
    scopes: tuple[str, ...]
    token_type: str = "Bearer"  # noqa: S105

    def expired(self, skew: timedelta = timedelta(seconds=60)) -> bool:
        if self.expiry is None:
            return False
        return datetime.now(UTC) + skew >= self.expiry


def prefer_existing_refresh(new: StoredTokens, existing: StoredTokens | None) -> StoredTokens:
    """Google often omits `refresh_token` on a reconnect. Keep the old one."""
    if existing is None:
        return new
    return StoredTokens(
        access_token=new.access_token,
        refresh_token=new.refresh_token or existing.refresh_token,
        expiry=new.expiry,
        scopes=new.scopes or existing.scopes,
        token_type=new.token_type,
    )


def store_tokens(account_id: str, tokens: StoredTokens) -> str:
    ref = account_token_ref(account_id)
    payload = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expiry": tokens.expiry.isoformat() if tokens.expiry is not None else None,
        "scopes": list(tokens.scopes),
        "token_type": tokens.token_type,
        "updated_at": utcnow_iso(),
    }
    store_secret(ref, json.dumps(payload))
    return ref


def load_tokens(credential_ref: str) -> StoredTokens:
    raw = get_secret(credential_ref)
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CredentialStoreError(f"Corrupt token payload for '{credential_ref}'.") from exc
    payload = as_dict(decoded)
    if payload is None:
        raise CredentialStoreError(f"Corrupt token payload for '{credential_ref}'.")
    access = as_str(payload.get("access_token"))
    if not access:
        raise CredentialStoreError(f"No access token stored for '{credential_ref}'.")
    refresh = as_str(payload.get("refresh_token"))
    expiry_raw = as_str(payload.get("expiry"))
    expiry: datetime | None = None
    if expiry_raw:
        try:
            expiry = datetime.fromisoformat(expiry_raw)
        except ValueError:
            expiry = None
        else:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
    token_type = as_str(payload.get("token_type"))
    return StoredTokens(
        access_token=access,
        refresh_token=refresh or None,
        expiry=expiry,
        scopes=tuple(string_list(payload.get("scopes"))),
        token_type=token_type or "Bearer",
    )


def delete_tokens(credential_ref: str) -> None:
    delete_secret(credential_ref)
