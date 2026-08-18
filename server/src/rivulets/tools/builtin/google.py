"""Google Workspace built-in tools (#458).

Thin wrappers: they resolve the stored token at invocation time and
never take a secret as a model-visible argument. Read tools (search /
read / list) and write tools (draft / send / create) are separate so an
owner can assign Gmail read without send-as-me.

All of these require the `integrations:google` scope. Write tools are
also `sensitive` so unattended send/create goes through the existing
approval queue.

OAuth client id/secret needed for refresh live in a process cache the
API writes (and startup reloads). Tools cannot open the async session
from a sync `@tool` while an event loop is already running.
"""

from __future__ import annotations

from collections.abc import Callable

from agno.tools import tool

from rivulets.integrations.google import (
    GoogleAuthExpiredError,
    GoogleIntegrationError,
    GoogleNotConnectedError,
    cached_oauth_client,
    calendar_create,
    calendar_list,
    gmail_draft,
    gmail_read,
    gmail_search,
    gmail_send,
    resolve_access_token,
)


def _access_token(account: str | None) -> str:
    client_id, client_secret = cached_oauth_client()
    _connected, token = resolve_access_token(
        account=account, client_id=client_id, client_secret=client_secret
    )
    return token


def _run(account: str | None, fn: Callable[[str], str]) -> str:
    try:
        token = _access_token(account)
    except GoogleNotConnectedError as exc:
        return str(exc)
    except GoogleAuthExpiredError as exc:
        return str(exc)
    except GoogleIntegrationError as exc:
        return str(exc)
    return fn(token)


@tool
def google_gmail_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search the connected Google account's Gmail. `query` uses Gmail's
    search syntax (from:, subject:, newer_than:, etc.). Returns message
    ids plus From/Date/Subject. Use google_gmail_read to open one."""
    return _run(account or None, lambda token: gmail_search(token, query, max_results))


@tool
def google_gmail_read(message_id: str, account: str = "") -> str:
    """Read one Gmail message by id (from google_gmail_search). Returns
    headers and the text body."""
    return _run(account or None, lambda token: gmail_read(token, message_id))


@tool
def google_gmail_draft(to: str, subject: str, body: str, account: str = "") -> str:
    """Create a Gmail draft addressed to `to`. Does not send. Sending is
    google_gmail_send."""
    return _run(account or None, lambda token: gmail_draft(token, to, subject, body))


@tool
def google_gmail_send(to: str, subject: str, body: str, account: str = "") -> str:
    """Send an email from the connected Google account to `to`. This
    sends immediately — use google_gmail_draft to stage a message
    instead."""
    return _run(account or None, lambda token: gmail_send(token, to, subject, body))


@tool
def google_calendar_list(
    time_min: str = "", time_max: str = "", max_results: int = 10, account: str = ""
) -> str:
    """List events on the connected Google account's primary calendar.
    `time_min` and `time_max` are RFC3339 timestamps; omit `time_min`
    to start from now."""
    return _run(
        account or None,
        lambda token: calendar_list(
            token,
            time_min=time_min or None,
            time_max=time_max or None,
            max_results=max_results,
        ),
    )


@tool
def google_calendar_create(
    summary: str, start: str, end: str, description: str = "", account: str = ""
) -> str:
    """Create an event on the connected Google account's primary
    calendar. `start` and `end` are RFC3339 date-times or YYYY-MM-DD
    all-day dates."""
    return _run(
        account or None,
        lambda token: calendar_create(
            token,
            summary=summary,
            start=start,
            end=end,
            description=description or None,
        ),
    )
