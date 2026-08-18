"""Owner-only integration accounts (Google first, #458).

OAuth is owner-only: an invite-grant session must not connect or
reconnect. Tokens go to the credential store; this router never returns
them. The callback is the one unauthenticated route — Google redirects
the browser to loopback with no Authorization header — and is gated by
an unguessable `state` minted at connect time. Reconnect updates the
same account row and credential ref so a scope grant does not create
a second connection.
"""

from __future__ import annotations

import json
import logging
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.base import utcnow_iso, uuid7
from rivulets.db.models import IntegrationAccount, IntegrationOAuthApp
from rivulets.integrations.google import (
    PROVIDER,
    GoogleIntegrationError,
    cache_oauth_client,
    callback_redirect_uri,
    exchange_code,
    fetch_account_email,
    load_client_secret,
    revoke_token,
    start_authorization,
    take_pending,
)
from rivulets.integrations.jsonutil import string_list
from rivulets.integrations.registry import (
    account_from_row,
    load_integration_registry,
    remove_connected_account,
    upsert_connected_account,
)
from rivulets.integrations.tokens import (
    StoredTokens,
    delete_tokens,
    load_tokens,
    oauth_app_secret_ref,
    prefer_existing_refresh,
    store_tokens,
)
from rivulets.security.credentials import (
    CredentialStoreError,
    delete_secret,
    store_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


class GoogleOAuthAppIn(BaseModel):
    client_id: str
    client_secret: str | None = None


class GoogleOAuthAppOut(BaseModel):
    provider: Literal["google"]
    client_id: str
    has_client_secret: bool
    redirect_uri: str


class IntegrationAccountOut(BaseModel):
    id: str
    provider: str
    label: str
    account_email: str | None
    status: str
    scopes: list[str]
    last_error: str | None

    model_config = {"from_attributes": True}


class GoogleConnectIn(BaseModel):
    label: str | None = None


class GoogleConnectOut(BaseModel):
    authorization_url: str


class IntegrationAccountUpdate(BaseModel):
    label: str | None = None


def _account_out(row: IntegrationAccount) -> IntegrationAccountOut:
    try:
        parsed: object = json.loads(row.scopes_json)
    except json.JSONDecodeError:
        parsed = []
    scopes = string_list(parsed)
    return IntegrationAccountOut(
        id=row.id,
        provider=row.provider,
        label=row.label,
        account_email=row.account_email,
        status=row.status,
        scopes=scopes,
        last_error=row.last_error,
    )


async def _google_oauth_app(db: DbSession) -> IntegrationOAuthApp | None:
    return await db.get(IntegrationOAuthApp, PROVIDER)


# Settings tabs live in the SPA URL (#471 / #464). The callback is a
# full-page load outside the SPA, so it has to name the Integrations tab
# or the owner lands on Safety and thinks connect failed.
_SETTINGS_INTEGRATIONS = "/settings?tab=integrations"


def _callback_page(title: str, body: str, *, ok: bool) -> HTMLResponse:
    tone = "Connected." if ok else "Couldn't connect."
    dest = _SETTINGS_INTEGRATIONS
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 36rem;
           margin: 4rem auto; padding: 0 1.5rem; }}
    a {{ color: #0b6e4f; }}
  </style>
</head>
<body>
  <h1>{escape(tone)}</h1>
  <p>{escape(body)}</p>
  <p><a href="{dest}">Back to Settings</a></p>
  {f"<script>location.replace('{dest}');</script>" if ok else ""}
</body>
</html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.get("", response_model=list[IntegrationAccountOut])
async def list_integrations(
    db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> list[IntegrationAccountOut]:
    result = await db.execute(select(IntegrationAccount).order_by(IntegrationAccount.created_at))
    return [_account_out(row) for row in result.scalars().all()]


@router.get("/google/oauth-app", response_model=GoogleOAuthAppOut)
async def get_google_oauth_app(
    db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> GoogleOAuthAppOut:
    row = await _google_oauth_app(db)
    return GoogleOAuthAppOut(
        provider="google",
        client_id=row.client_id if row is not None else "",
        has_client_secret=bool(row is not None and row.client_secret_ref),
        redirect_uri=callback_redirect_uri(),
    )


@router.put("/google/oauth-app", response_model=GoogleOAuthAppOut)
async def put_google_oauth_app(
    body: GoogleOAuthAppIn, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> GoogleOAuthAppOut:
    client_id = body.client_id.strip()
    if not client_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Client ID is required.")
    row = await _google_oauth_app(db)
    secret_ref = oauth_app_secret_ref(PROVIDER)
    if row is None:
        row = IntegrationOAuthApp(provider=PROVIDER, client_id=client_id)
        db.add(row)
    else:
        row.client_id = client_id
        row.updated_at = utcnow_iso()
    if body.client_secret is not None:
        stripped = body.client_secret.strip()
        if stripped:
            try:
                store_secret(secret_ref, stripped)
            except CredentialStoreError as exc:
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
            row.client_secret_ref = secret_ref
        else:
            delete_secret(secret_ref)
            row.client_secret_ref = None
    await db.commit()
    await db.refresh(row)
    cache_oauth_client(row.client_id, load_client_secret(row.client_secret_ref))
    return GoogleOAuthAppOut(
        provider="google",
        client_id=row.client_id,
        has_client_secret=bool(row.client_secret_ref),
        redirect_uri=callback_redirect_uri(),
    )


async def _require_google_oauth_app(db: DbSession) -> IntegrationOAuthApp:
    row = await _google_oauth_app(db)
    if row is None or not row.client_id.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Add a Google OAuth client ID in Settings before connecting an account.",
        )
    return row


def _authorization_url(
    app_row: IntegrationOAuthApp,
    *,
    label: str,
    account_id: str | None = None,
    login_hint: str | None = None,
) -> str:
    return start_authorization(
        label,
        app_row.client_id.strip(),
        account_id=account_id,
        login_hint=login_hint,
    )


@router.post("/google/connect", response_model=GoogleConnectOut)
async def connect_google(
    body: GoogleConnectIn, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> GoogleConnectOut:
    app_row = await _require_google_oauth_app(db)
    label = (body.label or "").strip() or "Google"
    return GoogleConnectOut(authorization_url=_authorization_url(app_row, label=label))


@router.post("/{account_id}/reconnect", response_model=GoogleConnectOut)
async def reconnect_integration(
    account_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> GoogleConnectOut:
    row = await db.get(IntegrationAccount, account_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    if row.provider != PROVIDER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Reconnect is only implemented for Google.",
        )
    app_row = await _require_google_oauth_app(db)
    return GoogleConnectOut(
        authorization_url=_authorization_url(
            app_row,
            label=row.label,
            account_id=row.id,
            login_hint=row.account_email,
        )
    )


@router.get("/google/callback")
async def google_callback(
    db: DbSession, code: str | None = None, state: str | None = None, error: str | None = None
) -> HTMLResponse:
    if error:
        return _callback_page("Google", error.replace("_", " "), ok=False)
    if not code or not state:
        return _callback_page("Google", "Google did not return a sign-in code.", ok=False)
    try:
        pending = take_pending(state)
    except GoogleIntegrationError as exc:
        return _callback_page("Google", str(exc), ok=False)

    app_row = await _google_oauth_app(db)
    if app_row is None:
        return _callback_page("Google", "No Google OAuth client is configured.", ok=False)
    client_secret = load_client_secret(app_row.client_secret_ref)
    try:
        tokens = exchange_code(
            code=code,
            code_verifier=pending.code_verifier,
            client_id=app_row.client_id,
            client_secret=client_secret,
        )
    except GoogleIntegrationError as exc:
        return _callback_page("Google", str(exc), ok=False)

    email = fetch_account_email(tokens.access_token)
    existing = await _account_for_callback(db, account_id=pending.account_id, email=email)
    if pending.account_id and existing is None:
        return _callback_page(
            "Google",
            "That connected account was removed before sign-in finished.",
            ok=False,
        )
    if existing is not None and email:
        clash = await _google_account_by_email(db, email)
        if clash is not None and clash.id != existing.id:
            return _callback_page(
                "Google",
                f"{email} is already connected as {clash.label}. "
                "Disconnect that account first, or pick the matching Google user.",
                ok=False,
            )

    if existing is not None:
        stored = _existing_tokens(existing.credential_ref)
        tokens = prefer_existing_refresh(tokens, stored)
        account_id = existing.id
        row = existing
        created = False
    else:
        account_id = uuid7()
        row = None
        created = True

    try:
        credential_ref = store_tokens(account_id, tokens)
    except CredentialStoreError as exc:
        return _callback_page("Google", str(exc), ok=False)

    label = pending.label
    if email and label == "Google":
        label = email
    if created:
        row = IntegrationAccount(
            id=account_id,
            provider=PROVIDER,
            label=label,
            account_email=email,
            status="connected",
            scopes_json=json.dumps(list(tokens.scopes)),
            credential_ref=credential_ref,
            last_error=None,
        )
        db.add(row)
    else:
        assert row is not None
        if pending.label and pending.label != "Google":
            row.label = pending.label
        elif email and row.label in {"Google", row.account_email}:
            row.label = email
        row.account_email = email or row.account_email
        row.status = "connected"
        row.scopes_json = json.dumps(list(tokens.scopes))
        row.credential_ref = credential_ref
        row.last_error = None
        row.updated_at = utcnow_iso()
    await db.commit()
    await db.refresh(row)
    loaded = account_from_row(row)
    if loaded is not None:
        upsert_connected_account(loaded)
    who = email or "Google"
    verb = "reconnected" if not created else "connected"
    return _callback_page("Google", f"{who} is {verb}. You can close this tab.", ok=True)


async def _google_account_by_email(db: DbSession, email: str) -> IntegrationAccount | None:
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.provider == PROVIDER,
            IntegrationAccount.account_email == email,
        )
    )
    return result.scalars().first()


async def _account_for_callback(
    db: DbSession, *, account_id: str | None, email: str | None
) -> IntegrationAccount | None:
    if account_id:
        row = await db.get(IntegrationAccount, account_id)
        if row is not None and row.provider == PROVIDER:
            return row
        return None
    if email:
        return await _google_account_by_email(db, email)
    return None


def _existing_tokens(credential_ref: str | None) -> StoredTokens | None:
    if not credential_ref:
        return None
    try:
        return load_tokens(credential_ref)
    except CredentialStoreError:
        return None


@router.patch("/{account_id}", response_model=IntegrationAccountOut)
async def update_integration(
    account_id: str,
    body: IntegrationAccountUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
    _o: OwnerGrant,
) -> IntegrationAccountOut:
    row = await db.get(IntegrationAccount, account_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    if body.label is not None:
        stripped = body.label.strip()
        if not stripped:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Label is required.")
        row.label = stripped
        row.updated_at = utcnow_iso()
    await db.commit()
    await db.refresh(row)
    loaded = account_from_row(row)
    if loaded is not None:
        upsert_connected_account(loaded)
    return _account_out(row)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_integration(
    account_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> None:
    row = await db.get(IntegrationAccount, account_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    if row.credential_ref:
        try:
            stored = load_tokens(row.credential_ref)
        except CredentialStoreError:
            stored = None
        if stored is not None:
            revoke_token(stored.refresh_token or stored.access_token)
        delete_tokens(row.credential_ref)
    remove_connected_account(row.id)
    await db.delete(row)
    await db.commit()


# Reloaded on startup from app.py; imported here so tests that only
# import this module still see the name.
__all__ = ["router", "load_integration_registry"]
