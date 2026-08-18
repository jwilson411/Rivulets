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
from urllib.parse import quote, urlencode

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
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
DOCS_API = "https://docs.googleapis.com/v1/documents"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
CONTACTS_API = "https://people.googleapis.com/v1"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
MEET_API = "https://meet.googleapis.com/v2"

SCOPE_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
SCOPE_DRIVE = "https://www.googleapis.com/auth/drive"
SCOPE_DOCS_READONLY = "https://www.googleapis.com/auth/documents.readonly"
SCOPE_DOCS = "https://www.googleapis.com/auth/documents"
SCOPE_SHEETS_READONLY = "https://www.googleapis.com/auth/spreadsheets.readonly"
SCOPE_SHEETS = "https://www.googleapis.com/auth/spreadsheets"
SCOPE_CONTACTS_READONLY = "https://www.googleapis.com/auth/contacts.readonly"
SCOPE_CONTACTS_OTHER_READONLY = "https://www.googleapis.com/auth/contacts.other.readonly"
SCOPE_TASKS_READONLY = "https://www.googleapis.com/auth/tasks.readonly"
SCOPE_TASKS = "https://www.googleapis.com/auth/tasks"
SCOPE_MEET_SPACE_CREATED = "https://www.googleapis.com/auth/meetings.space.created"
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
    SCOPE_DRIVE_READONLY,
    SCOPE_DRIVE,
    SCOPE_DOCS_READONLY,
    SCOPE_DOCS,
    SCOPE_SHEETS_READONLY,
    SCOPE_SHEETS,
    SCOPE_CONTACTS_READONLY,
    SCOPE_CONTACTS_OTHER_READONLY,
    SCOPE_TASKS_READONLY,
    SCOPE_TASKS,
    SCOPE_MEET_SPACE_CREATED,
)

_MAX_READ_CHARS = 20_000
_MAX_MEDIA_BYTES = 1_000_000
_GOOGLE_DOC = "application/vnd.google-apps.document"
_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDE = "application/vnd.google-apps.presentation"

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
    account_id: str | None = None


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


def start_authorization(
    label: str,
    client_id: str,
    *,
    account_id: str | None = None,
    login_hint: str | None = None,
    scopes: tuple[str, ...] | None = None,
) -> str:
    """Remember a PKCE verifier under a CSRF `state` and return Google's URL.

    `account_id` marks an in-place reconnect so the callback updates that
    row instead of inserting a second account. `login_hint` steers the
    Google picker toward the already-connected email. `scopes` defaults
    to the full Workspace set; the Settings API usually passes the
    owner-selected subset.
    """
    now = time.monotonic()
    _purge_expired_pending(now)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    _pending[state] = PendingOAuth(
        label=label,
        code_verifier=verifier,
        created_at=now,
        account_id=account_id,
    )
    params = {
        "client_id": client_id,
        "redirect_uri": callback_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes if scopes is not None else CONNECT_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if login_hint and login_hint.strip():
        params["login_hint"] = login_hint.strip()
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
    description = as_str(payload.get("error_description"))
    if not description:
        error = payload.get("error")
        if isinstance(error, str):
            description = error
        else:
            nested = as_dict(error) or {}
            description = as_str(nested.get("message"))
    if not description or not description.strip():
        description = fallback
    if response.status_code == 403 and "scope" in description.lower():
        return (
            f"{description} Reconnect the account in Settings → Integrations "
            "to grant the missing Google Workspace scopes."
        )
    return description


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


def calendar_update(
    access_token: str,
    event_id: str,
    *,
    summary: str | None,
    start: str | None,
    end: str | None,
    description: str | None,
) -> str:
    body: dict[str, object] = {}
    if summary and summary.strip():
        body["summary"] = summary.strip()
    if start and start.strip():
        body["start"] = _event_body_time(start)
    if end and end.strip():
        body["end"] = _event_body_time(end)
    if description is not None:
        body["description"] = description
    if not body:
        return "Nothing to update — pass a summary, start, end, or description."
    response = google_request(
        "PATCH",
        f"{CALENDAR_API}/events/{event_id.strip()}",
        access_token=access_token,
        json_body=body,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't update the calendar event.")
    payload = as_dict(response.json()) or {}
    updated_id = as_str(payload.get("id")) or event_id
    html_link = as_str(payload.get("htmlLink")) or ""
    return f"Event updated. event_id={updated_id}\n{html_link}".rstrip()


def drive_search(access_token: str, query: str, max_results: int) -> str:
    bounded = max(1, min(max_results, 25))
    response = google_request(
        "GET",
        f"{DRIVE_API}/files",
        access_token=access_token,
        params={
            "q": query,
            "pageSize": bounded,
            "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)",
            "spaces": "drive",
        },
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Drive search failed.")
    payload = as_dict(response.json()) or {}
    files = as_list(payload.get("files")) or []
    if not files:
        return "No files matched."
    lines: list[str] = []
    for item in files[:bounded]:
        row = as_dict(item)
        if row is None:
            continue
        file_id = as_str(row.get("id")) or ""
        name = as_str(row.get("name")) or "(untitled)"
        mime = as_str(row.get("mimeType")) or ""
        modified = as_str(row.get("modifiedTime")) or ""
        link = as_str(row.get("webViewLink")) or ""
        lines.append(f"{file_id}\n{name}\n{mime}\n{modified}\n{link}".rstrip())
    return "\n\n".join(lines) if lines else "No files matched."


def drive_read(access_token: str, file_id: str) -> str:
    file_id = file_id.strip()
    meta_response = google_request(
        "GET",
        f"{DRIVE_API}/files/{file_id}",
        access_token=access_token,
        params={"fields": "id,name,mimeType,modifiedTime,size,webViewLink,description"},
    )
    if meta_response.status_code >= 400:
        return _google_error_message(meta_response, "Couldn't read that Drive file.")
    meta = as_dict(meta_response.json()) or {}
    name = as_str(meta.get("name")) or "(untitled)"
    mime = as_str(meta.get("mimeType")) or ""
    modified = as_str(meta.get("modifiedTime")) or ""
    link = as_str(meta.get("webViewLink")) or ""
    description = as_str(meta.get("description")) or ""
    header = [f"id: {file_id}", f"name: {name}", f"mimeType: {mime}"]
    if modified:
        header.append(f"modified: {modified}")
    if link:
        header.append(link)
    if description:
        header.append(description)

    export_mime = _drive_export_mime(mime)
    if export_mime is not None:
        exported = google_request_bytes(
            "GET",
            f"{DRIVE_API}/files/{file_id}/export",
            access_token=access_token,
            params={"mimeType": export_mime},
        )
        if exported.status_code >= 400:
            return (
                "\n".join(header)
                + "\n\n"
                + _google_error_message(exported, "Couldn't export that Google file.")
            )
        return _clip("\n".join(header) + "\n\n" + exported.text)

    if not _looks_like_text(mime):
        return "\n".join(header) + "\n\nBinary file — content not shown."
    size = meta.get("size")
    if isinstance(size, str) and size.isdigit() and int(size) > _MAX_MEDIA_BYTES:
        return "\n".join(header) + "\n\nFile is too large to read here."
    media = google_request_bytes(
        "GET",
        f"{DRIVE_API}/files/{file_id}",
        access_token=access_token,
        params={"alt": "media"},
    )
    if media.status_code >= 400:
        return (
            "\n".join(header)
            + "\n\n"
            + _google_error_message(media, "Couldn't download that Drive file.")
        )
    return _clip("\n".join(header) + "\n\n" + media.text)


def drive_write(
    access_token: str,
    *,
    name: str,
    content: str,
    file_id: str | None,
    mime_type: str | None,
) -> str:
    mime = (mime_type or "").strip() or "text/plain"
    existing = (file_id or "").strip()
    if existing:
        if name.strip():
            renamed = google_request(
                "PATCH",
                f"{DRIVE_API}/files/{existing}",
                access_token=access_token,
                json_body={"name": name.strip()},
            )
            if renamed.status_code >= 400:
                return _google_error_message(renamed, "Couldn't rename that Drive file.")
        if content:
            uploaded = google_request_bytes(
                "PATCH",
                f"{DRIVE_UPLOAD_API}/files/{existing}",
                access_token=access_token,
                params={"uploadType": "media"},
                content=content.encode("utf-8"),
                content_type=mime,
            )
            if uploaded.status_code >= 400:
                return _google_error_message(uploaded, "Couldn't update that Drive file.")
        return f"File updated. file_id={existing}"

    created = google_request(
        "POST",
        f"{DRIVE_API}/files",
        access_token=access_token,
        json_body={"name": name.strip() or "Untitled", "mimeType": mime},
    )
    if created.status_code >= 400:
        return _google_error_message(created, "Couldn't create the Drive file.")
    payload = as_dict(created.json()) or {}
    new_id = as_str(payload.get("id"))
    if not new_id:
        return "Couldn't create the Drive file."
    if content:
        uploaded = google_request_bytes(
            "PATCH",
            f"{DRIVE_UPLOAD_API}/files/{new_id}",
            access_token=access_token,
            params={"uploadType": "media"},
            content=content.encode("utf-8"),
            content_type=mime,
        )
        if uploaded.status_code >= 400:
            return _google_error_message(
                uploaded, "Created the file but couldn't write its content."
            )
    return f"File created. file_id={new_id}"


def docs_read(access_token: str, document_id: str) -> str:
    response = google_request(
        "GET",
        f"{DOCS_API}/{document_id.strip()}",
        access_token=access_token,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't read that Google Doc.")
    payload = as_dict(response.json()) or {}
    title = as_str(payload.get("title")) or "(untitled)"
    text = _docs_text(payload)
    return _clip(f"{title}\n\n{text}".rstrip())


def docs_append(access_token: str, document_id: str, text: str) -> str:
    document_id = document_id.strip()
    fetched = google_request(
        "GET",
        f"{DOCS_API}/{document_id}",
        access_token=access_token,
        params={"fields": "body(content(endIndex)),title"},
    )
    if fetched.status_code >= 400:
        return _google_error_message(fetched, "Couldn't open that Google Doc.")
    payload = as_dict(fetched.json()) or {}
    end_index = _docs_end_index(payload)
    insert_at = max(1, end_index - 1)
    to_insert = text if text.startswith("\n") or insert_at == 1 else f"\n{text}"
    response = google_request(
        "POST",
        f"{DOCS_API}/{document_id}:batchUpdate",
        access_token=access_token,
        json_body={
            "requests": [{"insertText": {"location": {"index": insert_at}, "text": to_insert}}]
        },
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't append to that Google Doc.")
    title = as_str(payload.get("title")) or document_id
    return f"Appended to {title}."


def sheets_read(access_token: str, spreadsheet_id: str, range_a1: str) -> str:
    bounded_range = range_a1.strip() or "A1:Z100"
    response = google_request(
        "GET",
        f"{SHEETS_API}/{spreadsheet_id.strip()}/values/{quote(bounded_range, safe='')}",
        access_token=access_token,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't read that Google Sheet.")
    payload = as_dict(response.json()) or {}
    values = as_list(payload.get("values")) or []
    if not values:
        return "Sheet range is empty."
    lines: list[str] = []
    for row in values:
        cells = as_list(row) or []
        lines.append("\t".join(as_str(cell) or "" for cell in cells))
    resolved = as_str(payload.get("range")) or bounded_range
    return _clip(f"{resolved}\n\n" + "\n".join(lines))


def sheets_update(access_token: str, spreadsheet_id: str, range_a1: str, values: str) -> str:
    rows = _sheet_values(values)
    if not rows:
        return "No values to write."
    target = range_a1.strip() or "A1"
    response = google_request(
        "PUT",
        f"{SHEETS_API}/{spreadsheet_id.strip()}/values/{quote(target, safe='')}",
        access_token=access_token,
        params={"valueInputOption": "USER_ENTERED"},
        json_body={"range": target, "majorDimension": "ROWS", "values": rows},
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't update that Google Sheet.")
    payload = as_dict(response.json()) or {}
    updated = payload.get("updatedCells")
    updated_range = as_str(payload.get("updatedRange")) or target
    return f"Updated {updated_range} ({updated} cells)."


_CONTACTS_READ_MASK = "names,emailAddresses,phoneNumbers,organizations"
_MEET_ACCESS_TYPES = frozenset({"OPEN", "TRUSTED", "RESTRICTED"})


def contacts_search(access_token: str, query: str, max_results: int) -> str:
    q = query.strip()
    if not q:
        return "Pass a name, email, or phone to search."
    bounded = max(1, min(max_results, 25))
    response = google_request(
        "GET",
        f"{CONTACTS_API}/people:searchContacts",
        access_token=access_token,
        params={"query": q, "pageSize": bounded, "readMask": _CONTACTS_READ_MASK},
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Contacts search failed.")
    results = _contact_results(response.json())
    if not results:
        other = google_request(
            "GET",
            f"{CONTACTS_API}/otherContacts:search",
            access_token=access_token,
            params={
                "query": q,
                "pageSize": bounded,
                "readMask": "names,emailAddresses,phoneNumbers",
            },
        )
        if other.status_code >= 400:
            return _google_error_message(other, "Contacts search failed.")
        results = _contact_results(other.json())
    if not results:
        return "No contacts matched."
    return "\n\n".join(results[:bounded])


def tasks_list(
    access_token: str,
    *,
    task_list: str | None,
    max_results: int,
    include_completed: bool,
) -> str:
    bounded = max(1, min(max_results, 50))
    list_id = (task_list or "").strip()
    if list_id:
        return _tasks_for_list(
            access_token,
            list_id,
            heading=None,
            max_results=bounded,
            include_completed=include_completed,
        )
    lists_response = google_request(
        "GET",
        f"{TASKS_API}/users/@me/lists",
        access_token=access_token,
        params={"maxResults": 20},
    )
    if lists_response.status_code >= 400:
        return _google_error_message(lists_response, "Couldn't list task lists.")
    payload = as_dict(lists_response.json()) or {}
    lists = as_list(payload.get("items")) or []
    if not lists:
        return "No task lists."
    blocks: list[str] = []
    for item in lists:
        row = as_dict(item)
        if row is None:
            continue
        lid = as_str(row.get("id"))
        title = as_str(row.get("title")) or lid
        if not lid:
            continue
        blocks.append(
            _tasks_for_list(
                access_token,
                lid,
                heading=title,
                max_results=bounded,
                include_completed=include_completed,
            )
        )
    return "\n\n".join(blocks) if blocks else "No task lists."


def tasks_add(
    access_token: str,
    *,
    title: str,
    notes: str | None,
    due: str | None,
    task_list: str | None,
) -> str:
    stripped_title = title.strip()
    if not stripped_title:
        return "A task title is required."
    list_id = (task_list or "").strip() or "@default"
    body: dict[str, object] = {"title": stripped_title}
    if notes and notes.strip():
        body["notes"] = notes.strip()
    if due and due.strip():
        body["due"] = _task_due(due.strip())
    response = google_request(
        "POST",
        f"{TASKS_API}/lists/{quote(list_id, safe='@')}/tasks",
        access_token=access_token,
        json_body=body,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't add the task.")
    payload = as_dict(response.json()) or {}
    task_id = as_str(payload.get("id")) or ""
    return f"Task created. task_id={task_id} list_id={list_id}"


def meet_create(access_token: str, *, access_type: str | None) -> str:
    body: dict[str, object] = {}
    access = (access_type or "").strip().upper()
    if access:
        if access not in _MEET_ACCESS_TYPES:
            return "access_type must be OPEN, TRUSTED, or RESTRICTED."
        body["config"] = {"accessType": access}
    response = google_request(
        "POST",
        f"{MEET_API}/spaces",
        access_token=access_token,
        json_body=body or None,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't create a Meet link.")
    payload = as_dict(response.json()) or {}
    uri = as_str(payload.get("meetingUri")) or ""
    code = as_str(payload.get("meetingCode")) or ""
    name = as_str(payload.get("name")) or ""
    if not uri and not code:
        return "Couldn't create a Meet link."
    lines = ["Meet space created."]
    if uri:
        lines.append(uri)
    if code:
        lines.append(f"code={code}")
    if name:
        lines.append(name)
    return "\n".join(lines)


def google_request_bytes(
    method: str,
    url: str,
    *,
    access_token: str,
    params: dict[str, str | int] | None = None,
    content: bytes | None = None,
    content_type: str | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {access_token}"}
    if content_type:
        headers["Content-Type"] = content_type
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        return client.request(method, url, params=params, content=content, headers=headers)


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


def _clip(text: str) -> str:
    if len(text) <= _MAX_READ_CHARS:
        return text
    return f"{text[:_MAX_READ_CHARS]}\n…(truncated)"


def _drive_export_mime(mime: str) -> str | None:
    if mime == _GOOGLE_DOC or mime == _GOOGLE_SLIDE:
        return "text/plain"
    if mime == _GOOGLE_SHEET:
        return "text/csv"
    return None


def _looks_like_text(mime: str) -> bool:
    if mime.startswith("text/"):
        return True
    return mime in {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
    }


def _docs_text(payload: object) -> str:
    data = as_dict(payload) or {}
    body = as_dict(data.get("body")) or {}
    return _docs_structural_text(body.get("content"))


def _docs_structural_text(content: object) -> str:
    parts: list[str] = []
    for item in as_list(content) or []:
        row = as_dict(item)
        if row is None:
            continue
        paragraph = as_dict(row.get("paragraph"))
        if paragraph is not None:
            for element in as_list(paragraph.get("elements")) or []:
                el = as_dict(element) or {}
                run = as_dict(el.get("textRun")) or {}
                text = as_str(run.get("content"))
                if text:
                    parts.append(text)
            continue
        table = as_dict(row.get("table"))
        if table is not None:
            for table_row in as_list(table.get("tableRows")) or []:
                tr = as_dict(table_row) or {}
                for cell in as_list(tr.get("tableCells")) or []:
                    cell_data = as_dict(cell) or {}
                    parts.append(_docs_structural_text(cell_data.get("content")))
            continue
        toc = as_dict(row.get("tableOfContents"))
        if toc is not None:
            parts.append(_docs_structural_text(toc.get("content")))
    return "".join(parts)


def _docs_end_index(payload: object) -> int:
    data = as_dict(payload) or {}
    body = as_dict(data.get("body")) or {}
    last_end = 1
    for item in as_list(body.get("content")) or []:
        row = as_dict(item) or {}
        end = row.get("endIndex")
        if isinstance(end, int):
            last_end = end
    return last_end


def _contact_results(payload: object) -> list[str]:
    data = as_dict(payload) or {}
    rows = as_list(data.get("results")) or []
    formatted: list[str] = []
    for item in rows:
        row = as_dict(item)
        if row is None:
            continue
        person = as_dict(row.get("person")) or row
        resource = as_str(person.get("resourceName")) or ""
        name = _first_named(person.get("names"), "displayName")
        emails = _joined_values(person.get("emailAddresses"))
        phones = _joined_values(person.get("phoneNumbers"))
        org = _first_named(person.get("organizations"), "name")
        parts = [part for part in (resource, name, emails, phones, org) if part]
        if parts:
            formatted.append("\n".join(parts))
    return formatted


def _first_named(value: object, key: str) -> str:
    for item in as_list(value) or []:
        row = as_dict(item)
        if row is None:
            continue
        text = as_str(row.get(key))
        if text:
            return text
    return ""


def _joined_values(value: object) -> str:
    found: list[str] = []
    for item in as_list(value) or []:
        row = as_dict(item)
        if row is None:
            continue
        text = as_str(row.get("value"))
        if text:
            found.append(text)
    return ", ".join(found)


def _tasks_for_list(
    access_token: str,
    list_id: str,
    *,
    heading: str | None,
    max_results: int,
    include_completed: bool,
) -> str:
    params: dict[str, str | int] = {"maxResults": max_results}
    if include_completed:
        params["showCompleted"] = "true"
        params["showHidden"] = "true"
    else:
        params["showCompleted"] = "false"
    response = google_request(
        "GET",
        f"{TASKS_API}/lists/{quote(list_id, safe='@')}/tasks",
        access_token=access_token,
        params=params,
    )
    if response.status_code >= 400:
        return _google_error_message(response, "Couldn't list tasks.")
    payload = as_dict(response.json()) or {}
    items = as_list(payload.get("items")) or []
    title = heading or list_id
    header = f"{list_id}\n{title}"
    if not items:
        return f"{header}\nNo tasks."
    lines = [header]
    for item in items[:max_results]:
        row = as_dict(item)
        if row is None:
            continue
        task_id = as_str(row.get("id")) or ""
        task_title = as_str(row.get("title")) or "(no title)"
        status = as_str(row.get("status")) or ""
        due = as_str(row.get("due")) or ""
        notes = as_str(row.get("notes")) or ""
        detail = f"{task_id}\n{task_title}"
        extras = " ".join(part for part in (status, f"due={due}" if due else "") if part)
        if extras:
            detail = f"{detail}  {extras}"
        if notes:
            detail = f"{detail}\n{notes}"
        lines.append(detail)
    return "\n".join(lines)


def _task_due(value: str) -> str:
    if "T" in value:
        return value
    return f"{value}T00:00:00.000Z"


def _sheet_values(raw: str) -> list[list[str]]:
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed: object = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        rows = as_list(parsed)
        if rows is not None:
            result: list[list[str]] = []
            for row in rows:
                cells = as_list(row)
                if cells is None:
                    result.append([as_str(row) or ""])
                else:
                    result.append([as_str(cell) or "" for cell in cells])
            return result
    return [line.split("\t") for line in stripped.splitlines()]


def load_client_secret(client_secret_ref: str | None) -> str | None:
    if not client_secret_ref:
        return None
    try:
        secret = get_secret(client_secret_ref)
    except CredentialStoreError:
        return None
    stripped = secret.strip()
    return stripped or None
