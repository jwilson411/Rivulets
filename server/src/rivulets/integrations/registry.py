"""In-process index of connected integration accounts.

The Settings API is the writer (and `load_integration_registry` reloads
from SQLite on startup). Built-in tools are the readers: they are sync
Agno `@tool` callables with no request-scoped session, and the test
suite's in-memory engine is invisible to a raw `sqlite3` open of
`db_path`, so tools must not go to the file DB themselves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import IntegrationAccount
from rivulets.integrations.jsonutil import string_list


@dataclass(frozen=True, slots=True)
class ConnectedAccount:
    id: str
    provider: str
    label: str
    account_email: str | None
    status: str
    scopes: tuple[str, ...]
    credential_ref: str


_accounts: dict[str, ConnectedAccount] = {}


def reset_integration_registry_for_testing() -> None:
    _accounts.clear()


def upsert_connected_account(account: ConnectedAccount) -> None:
    _accounts[account.id] = account


def remove_connected_account(account_id: str) -> None:
    _accounts.pop(account_id, None)


def list_connected_accounts(provider: str) -> list[ConnectedAccount]:
    return [
        account
        for account in _accounts.values()
        if account.provider == provider and account.status == "connected"
    ]


def get_connected_account(provider: str, account: str | None = None) -> ConnectedAccount | None:
    """Pick a connected account. `account` matches id, email, or label."""
    matches = list_connected_accounts(provider)
    if not matches:
        return None
    if account is None or not account.strip():
        return matches[0]
    needle = account.strip().lower()
    for row in matches:
        if row.id.lower() == needle:
            return row
        if row.account_email is not None and row.account_email.lower() == needle:
            return row
        if row.label.lower() == needle:
            return row
    return None


def account_from_row(row: IntegrationAccount) -> ConnectedAccount | None:
    if not row.credential_ref or row.status != "connected":
        return None
    try:
        parsed: object = json.loads(row.scopes_json)
    except json.JSONDecodeError:
        parsed = []
    scopes = tuple(string_list(parsed))
    return ConnectedAccount(
        id=row.id,
        provider=row.provider,
        label=row.label,
        account_email=row.account_email,
        status=row.status,
        scopes=scopes,
        credential_ref=row.credential_ref,
    )


async def load_integration_registry(db: AsyncSession) -> None:
    """Replace the in-process index from the DB. Called at startup."""
    from rivulets.db.models import IntegrationOAuthApp
    from rivulets.integrations.google import PROVIDER, cache_oauth_client, load_client_secret

    _accounts.clear()
    result = await db.execute(select(IntegrationAccount))
    for row in result.scalars().all():
        loaded = account_from_row(row)
        if loaded is not None:
            _accounts[loaded.id] = loaded
    app_row = await db.get(IntegrationOAuthApp, PROVIDER)
    if app_row is not None:
        cache_oauth_client(app_row.client_id, load_client_secret(app_row.client_secret_ref))
    else:
        cache_oauth_client("", None)
