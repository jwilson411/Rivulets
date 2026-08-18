"""Google OAuth + Workspace API helpers (#458).

Hosts are hardcoded to Google's public endpoints — tools never take a
URL argument, so this is not an SSRF surface the way `http_request` is.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

from rivulets.config import get_settings
from rivulets.integrations.jsonutil import as_dict, as_list, as_str
from rivulets.integrations.registry import ConnectedAccount, get_connected_account
from rivulets.integrations.tokens import StoredTokens, load_tokens, store_tokens
from rivulets.security.credentials import CredentialStoreError, get_secret

logger = logging.getLogger(__name__)

PROVIDER = "google"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary"

SCOPE_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_EMAIL = "email"
SCOPE_OPENID = "openid"

CONNECT_SCOPES: tuple[str, ...] = (
    SCOPE_OPENID,
    SCOPE_EMAIL,
    SCOPE_GMAIL_READONLY,
    SCOPE_GMAIL_COMPOSE,
    SCOPE_GMAIL_SEND,
    SCOPE_CALENDAR_READONLY,
    SCOPE_CALENDAR_EVENTS,
)

_PENDING_TTL_SECONDS = 600.0
_TIMEOUT_SECONDS = 20.0

# Written by the Settings API / startup reload. Tools read this instead
# of opening the async SQLAlchemy session from a sync `@tool`.
_oauth_client_cache: tuple[str, str | None] = ("", None)


def cache_oauth_client(client_id: str, client_secret: str | None) -> None:
    global _oauth_client_cache
    _oauth_client_cache = (client_id, client_secret)


def cached_oauth_client() -> tuple[str, str | None]:
    return _oauth_client_cache


def reset_oauth_client_cache_for_testing() -> None:
    cache_oauth_client("", None)


class GoogleIntegrationError(RuntimeError):
    pass


class GoogleNotConnectedError(GoogleIntegrationError):
    pass


class GoogleAuthExpiredError(GoogleIntegrationError):
    pass


@dataclass(frozen=True, slots=True)
class PendingOAuth:
    label: str
    code_verifier: str
    created_at: float


_pending: dict[str, PendingOAuth] = {}


def reset_pending_oauth_for_testing() -> None:
    _pending.clear()


def callback_redirect_uri() -> str:
    port = get_settings().app_server_port
    return f"http://127.0.0.1:{port}/api/v1/integrations/google/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _purge_expired_pending(now: float) -> None:
    expired = [
        state
        for state, pending in _pending.items()
        if now - pending.created_at > _PENDING_TTL_SECONDS
    ]
    for state in expired:
        del _pending[state]


def start_authorization(label: str, client_id: str) -> str:
    """Remember a PKCE verifier under a CSRF `state` and return Google's URL."""
    now = time.monotonic()
    _purge_expired_pending(now)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    _pending[state] = PendingOAuth(label=label, code_verifier=verifier, created_at=now)
    params = {
        "client_id": client_id,
        "redirect_uri": callback_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(CONNECT_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def take_pending(state: str) -> PendingOAuth:
    pending = _pending.pop(state, None)
    if pending is None:
        raise GoogleIntegrationError("This Google sign-in link is invalid or has expired.")
    if time.monotonic() - pending.created_at > _PENDING_TTL_SECONDS:
        raise GoogleIntegrationError("This Google sign-in link is invalid or has expired.")
    return pending


def exchange_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str | None,
) -> StoredTokens:
    data: dict[str, str] = {
        "code": code,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": callback_redirect_uri(),
    }
    if client_secret:
        data["client_secret"] = client_secret
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        response = client.post(TOKEN_ENDPOINT, data=data)
    if response.status_code >= 400:
        raise GoogleIntegrationError(
            _google_error_message(response, "Couldn't finish Google sign-in.")
        )
    return _tokens_from_response(response.json())


def refresh_tokens(
    tokens: StoredTokens, *, client_id: str, client_secret: str | None
) -> StoredTokens:
    if not tokens.refresh_token:
        raise GoogleAuthExpiredError(
            "Google access expired and no refresh token is stored. Reconnect in Settings."
        )
    data: dict[str, str] = {
        "refresh_token": tokens.refresh_token,
        "client_id": client_id,
        "grant_type": "refresh_token",
    }
    if client_secret:
        data["client_secret"] = client_secret
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        response = client.post(TOKEN_ENDPOINT, data=data)
    if response.status_code >= 400:
        raise GoogleAuthExpiredError(
            _google_error_message(response, "Google access expired. Reconnect in Settings.")
        )
    refreshed = _tokens_from_response(response.json())
    return StoredTokens(
        access_token=refreshed.access_token,
        refresh_token=refreshed.refresh_token or tokens.refresh_token,
        expiry=refreshed.expiry,
        scopes=refreshed.scopes or tokens.scopes,
        token_type=refreshed.token_type,
    )


def fetch_account_email(access_token: str) -> str | None:
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        response = client.get(
            USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
        )
    if response.status_code >= 400:
        logger.warning("Google userinfo failed: %s", response.text[:200])
        return None
    payload = as_dict(response.json())
    if payload is None:
        return None
    email = as_str(payload.get("email"))
    return email or None


def revoke_token(token: str) -> None:
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            client.post(REVOKE_ENDPOINT, data={"token": token})
    except httpx.HTTPError:
        logger.warning("Google token revoke failed", exc_info=True)


def _tokens_from_response(payload: object) -> StoredTokens:
    data = as_dict(payload)
    if data is None:
        raise GoogleIntegrationError("Google returned an unexpected token response.")
    access = as_str(data.get("access_token"))
    if not access:
        raise GoogleIntegrationError("Google did not return an access token.")
    refresh = as_str(data.get("refresh_token"))
    expires_in = data.get("expires_in")
    expiry: datetime | None = None
    if isinstance(expires_in, (int, float)):
        expiry = datetime.now(UTC) + timedelta(seconds=float(expires_in))
    scope_raw = as_str(data.get("scope"))
    scopes: tuple[str, ...] = ()
    if scope_raw and scope_raw.strip():
        scopes = tuple(scope_raw.split())
    token_type = as_str(data.get("token_type"))
    return StoredTokens(
        access_token=access,
        refresh_token=refresh or None,
        expiry=expiry,
        scopes=scopes,
        token_type=token_type or "Bearer",
    )


def _google_error_message(response: httpx.Response, fallback: str) -> str:
    try:
        payload = as_dict(response.json())
    except json.JSONDecodeError:
        return fallback
    if payload is None:
        return fallback
    description = as_str(payload.get("error_description")) or as_str(payload.get("error"))
    if description and description.strip():
        return description
    return fallback


def resolve_access_token(
    *,
    account: str | None,
    client_id: str,
    client_secret: str | None,
) -> tuple[ConnectedAccount, str]:
    connected = get_connected_account(PROVIDER, account)
    if connected is None:
        raise GoogleNotConnectedError(
            "No Google account is connected. Ask the workspace owner to connect one "
            "in Settings → Integrations."
        )
    try:
        tokens = load_tokens(connected.credential_ref)
    except CredentialStoreError as exc:
        raise GoogleAuthExpiredError(
            "Google credentials are missing. Reconnect the account in Settings."
        ) from exc
    if tokens.expired():
        tokens = refresh_tokens(tokens, client_id=client_id, client_secret=client_secret)
        store_tokens(connected.id, tokens)
    return connected, tokens.access_token


def google_request(
    method: str,
    url: str,
    *,
    access_token: str,
    params: dict[str, str | int] | None = None,
    json_body: dict[str, object] | None = None,
) -> httpx.Response:
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        return client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )


def gmail_search(access_token: str, query: str, max_results: int) -> str:
    bounded = max(1, min(max_results, 25))
    response = google_request(
        "GET",
        f"{GMAIL_API}/messages",
        access_token=access_token,
        params={"q": query, "maxResults": bounded},
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Gmail search failed.")
    payload = as_dict(response.json()) or {}
    messages = as_list(payload.get("messages")) or []
    if not messages:
        return "No messages matched."
    lines: list[str] = []
    for item in messages[:bounded]:
        row = as_dict(item)
        if row is None:
            continue
        message_id = as_str(row.get("id"))
        if not message_id:
            continue
        meta = google_request(
            "GET",
            f"{GMAIL_API}/messages/{message_id}",
            access_token=access_token,
            params={"format": "metadata", "metadataHeaders": "From,Subject,Date"},
        )
        if meta.status_code >= 400:
            lines.append(f"{message_id}\n(couldn't load headers)")
            continue
        headers = _gmail_headers(meta.json())
        lines.append(
            f"{message_id}\nFrom: {headers.get('From', '')}\n"
            f"Date: {headers.get('Date', '')}\nSubject: {headers.get('Subject', '')}"
        )
    return "\n\n".join(lines) if lines else "No messages matched."


def gmail_read(access_token: str, message_id: str) -> str:
    response = google_request(
        "GET",
        f"{GMAIL_API}/messages/{message_id}",
        access_token=access_token,
        params={"format": "full"},
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't read that Gmail message.")
    payload = as_dict(response.json()) or {}
    headers = _gmail_headers(payload)
    snippet = as_str(payload.get("snippet")) or ""
    body = _gmail_body(payload.get("payload"))
    parts = [
        f"From: {headers.get('From', '')}",
        f"To: {headers.get('To', '')}",
        f"Date: {headers.get('Date', '')}",
        f"Subject: {headers.get('Subject', '')}",
        "",
        body or snippet,
    ]
    return "\n".join(parts).strip()


def gmail_draft(access_token: str, to: str, subject: str, body: str) -> str:
    raw = _rfc822_raw(to, subject, body)
    response = google_request(
        "POST",
        f"{GMAIL_API}/drafts",
        access_token=access_token,
        json_body={"message": {"raw": raw}},
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't create a Gmail draft.")
    payload = as_dict(response.json()) or {}
    draft_id = as_str(payload.get("id"))
    message = as_dict(payload.get("message")) or {}
    message_id = as_str(message.get("id"))
    return f"Draft created. draft_id={draft_id} message_id={message_id}"


def gmail_send(access_token: str, to: str, subject: str, body: str) -> str:
    raw = _rfc822_raw(to, subject, body)
    response = google_request(
        "POST",
        f"{GMAIL_API}/messages/send",
        access_token=access_token,
        json_body={"raw": raw},
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't send the email.")
    payload = as_dict(response.json()) or {}
    message_id = as_str(payload.get("id"))
    return f"Email sent. message_id={message_id}"


def calendar_list(
    access_token: str,
    *,
    time_min: str | None,
    time_max: str | None,
    max_results: int,
) -> str:
    bounded = max(1, min(max_results, 25))
    params: dict[str, str | int] = {
        "maxResults": bounded,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    params["timeMin"] = time_min.strip() if time_min and time_min.strip() else _now_rfc3339()
    if time_max and time_max.strip():
        params["timeMax"] = time_max.strip()
    response = google_request(
        "GET",
        f"{CALENDAR_API}/events",
        access_token=access_token,
        params=params,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't list calendar events.")
    payload = as_dict(response.json()) or {}
    items = as_list(payload.get("items")) or []
    if not items:
        return "No upcoming events."
    lines: list[str] = []
    for item in items[:bounded]:
        event = as_dict(item)
        if event is None:
            continue
        summary = as_str(event.get("summary")) or "(no title)"
        start = _event_time(event.get("start"))
        end = _event_time(event.get("end"))
        event_id = as_str(event.get("id")) or ""
        html_link = as_str(event.get("htmlLink")) or ""
        lines.append(f"{event_id}\n{summary}\n{start} – {end}\n{html_link}".rstrip())
    return "\n\n".join(lines)


def calendar_create(
    access_token: str,
    *,
    summary: str,
    start: str,
    end: str,
    description: str | None,
) -> str:
    body: dict[str, object] = {
        "summary": summary,
        "start": _event_body_time(start),
        "end": _event_body_time(end),
    }
    if description and description.strip():
        body["description"] = description.strip()
    response = google_request(
        "POST",
        f"{CALENDAR_API}/events",
        access_token=access_token,
        json_body=body,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't create the calendar event.")
    payload = as_dict(response.json()) or {}
    event_id = as_str(payload.get("id"))
    html_link = as_str(payload.get("htmlLink")) or ""
    return f"Event created. event_id={event_id}\n{html_link}".rstrip()


def _now_rfc3339() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event_time(value: object) -> str:
    data = as_dict(value)
    if data is None:
        return ""
    return as_str(data.get("dateTime")) or as_str(data.get("date")) or ""


def _event_body_time(value: str) -> dict[str, str]:
    stripped = value.strip()
    if "T" in stripped:
        return {"dateTime": stripped}
    return {"date": stripped}


def _rfc822_raw(to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return encoded.rstrip("=")


def _gmail_headers(payload: object) -> dict[str, str]:
    data = as_dict(payload) or {}
    inner = as_dict(data.get("payload"))
    header_source = inner if inner is not None else data
    result: dict[str, str] = {}
    for item in as_list(header_source.get("headers")) or []:
        row = as_dict(item)
        if row is None:
            continue
        name = as_str(row.get("name"))
        value = as_str(row.get("value"))
        if name and value is not None:
            result[name] = value
    return result


def _gmail_body(payload: object) -> str:
    data = as_dict(payload)
    if data is None:
        return ""
    mime = as_str(data.get("mimeType"))
    body = as_dict(data.get("body")) or {}
    encoded = as_str(body.get("data"))
    if mime == "text/plain" and encoded:
        return _b64url_decode(encoded)
    parts = as_list(data.get("parts"))
    if parts is not None:
        plain = ""
        html = ""
        for part in parts:
            extracted = _gmail_body(part)
            if not extracted:
                continue
            part_data = as_dict(part) or {}
            part_mime = as_str(part_data.get("mimeType"))
            if part_mime == "text/plain" and not plain:
                plain = extracted
            elif part_mime == "text/html" and not html:
                html = extracted
            elif not plain:
                plain = extracted
        return plain or html
    if encoded:
        return _b64url_decode(encoded)
    return ""


def _b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def load_client_secret(client_secret_ref: str | None) -> str | None:
    if not client_secret_ref:
        return None
    try:
        secret = get_secret(client_secret_ref)
    except CredentialStoreError:
        return None
    stripped = secret.strip()
    return stripped or None
