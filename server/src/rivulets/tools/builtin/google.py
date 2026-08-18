"""Google Workspace built-in tools (#458).

Thin wrappers: they resolve the stored token at invocation time and
never take a secret as a model-visible argument. Read tools (search /
read / list) and write tools (draft / send / create / update / append /
add / meet create) are separate so an owner can assign Gmail read
without send-as-me, or Tasks list without Tasks add.

Read tools require the `integrations:google` scope. Write tools
(draft / send / create / update / append / add) require
`integrations:google:write` so connecting an account only enables
read until an owner grants write on a specific agent (#463). Write
tools are also `sensitive` so unattended send/create/write still
goes through the existing approval queue.

OAuth client id/secret needed for refresh live in a process cache the
API writes (and startup reloads). Tools cannot open the async session
from a sync `@tool` while an event loop is already running.
"""

from __future__ import annotations

from collections.abc import Callable

from agno.tools import tool

from rivulets.integrations.google import (
    SCOPE_CALENDAR_EVENTS,
    SCOPE_CALENDAR_READONLY,
    SCOPE_CONTACTS_READONLY,
    SCOPE_DOCS,
    SCOPE_DOCS_READONLY,
    SCOPE_DRIVE,
    SCOPE_DRIVE_READONLY,
    SCOPE_GMAIL_COMPOSE,
    SCOPE_GMAIL_READONLY,
    SCOPE_GMAIL_SEND,
    SCOPE_MEET_SPACE_CREATED,
    SCOPE_SHEETS,
    SCOPE_SHEETS_READONLY,
    SCOPE_TASKS,
    SCOPE_TASKS_READONLY,
    GoogleAuthExpiredError,
    GoogleIntegrationError,
    GoogleNotConnectedError,
    cached_oauth_client,
    calendar_create,
    calendar_list,
    calendar_update,
    contacts_search,
    docs_append,
    docs_read,
    drive_read,
    drive_search,
    drive_write,
    gmail_draft,
    gmail_read,
    gmail_search,
    gmail_send,
    meet_create,
    resolve_access_token,
    sheets_read,
    sheets_update,
    tasks_add,
    tasks_list,
)
from rivulets.integrations.google_capabilities import missing_scopes

_MISSING_ACCESS = (
    "This Google account was not granted that access. "
    "Reconnect in Settings → Integrations and check the matching box."
)


def _run(account: str | None, fn: Callable[[str], str], *needed: str) -> str:
    try:
        client_id, client_secret = cached_oauth_client()
        connected, token = resolve_access_token(
            account=account, client_id=client_id, client_secret=client_secret
        )
    except GoogleNotConnectedError as exc:
        return str(exc)
    except GoogleAuthExpiredError as exc:
        return str(exc)
    except GoogleIntegrationError as exc:
        return str(exc)
    if missing_scopes(connected.scopes, needed):
        return _MISSING_ACCESS
    return fn(token)


@tool
def google_gmail_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search the connected Google account's Gmail. `query` uses Gmail's
    search syntax (from:, subject:, newer_than:, etc.). Returns message
    ids plus From/Date/Subject. Use google_gmail_read to open one."""
    return _run(
        account or None,
        lambda token: gmail_search(token, query, max_results),
        SCOPE_GMAIL_READONLY,
    )


@tool
def google_gmail_read(message_id: str, account: str = "") -> str:
    """Read one Gmail message by id (from google_gmail_search). Returns
    headers and the text body."""
    return _run(account or None, lambda token: gmail_read(token, message_id), SCOPE_GMAIL_READONLY)


@tool
def google_gmail_draft(to: str, subject: str, body: str, account: str = "") -> str:
    """Create a Gmail draft addressed to `to`. Does not send. Sending is
    google_gmail_send."""
    return _run(
        account or None,
        lambda token: gmail_draft(token, to, subject, body),
        SCOPE_GMAIL_COMPOSE,
    )


@tool
def google_gmail_send(to: str, subject: str, body: str, account: str = "") -> str:
    """Send an email from the connected Google account to `to`. This
    sends immediately — use google_gmail_draft to stage a message
    instead."""
    return _run(
        account or None,
        lambda token: gmail_send(token, to, subject, body),
        SCOPE_GMAIL_SEND,
    )


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
        SCOPE_CALENDAR_READONLY,
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
        SCOPE_CALENDAR_EVENTS,
    )


@tool
def google_calendar_update(
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    account: str = "",
) -> str:
    """Update an event on the connected Google account's primary
    calendar (event_id from google_calendar_list). Pass only the
    fields to change. `start` and `end` are RFC3339 date-times or
    YYYY-MM-DD all-day dates."""
    return _run(
        account or None,
        lambda token: calendar_update(
            token,
            event_id,
            summary=summary or None,
            start=start or None,
            end=end or None,
            description=description if description else None,
        ),
        SCOPE_CALENDAR_EVENTS,
    )


@tool
def google_drive_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search the connected Google account's Drive. `query` uses Drive's
    search syntax (name contains 'x', mimeType = '...'). Returns file
    ids plus name/type. Use google_drive_read, google_docs_read, or
    google_sheets_read to open one."""
    return _run(
        account or None,
        lambda token: drive_search(token, query, max_results),
        SCOPE_DRIVE_READONLY,
    )


@tool
def google_drive_read(file_id: str, account: str = "") -> str:
    """Read one Drive file by id (from google_drive_search). Google
    Docs/Sheets/Slides are exported as text; other text files are
    downloaded. Binary files return metadata only."""
    return _run(account or None, lambda token: drive_read(token, file_id), SCOPE_DRIVE_READONLY)


@tool
def google_drive_write(
    name: str,
    content: str = "",
    file_id: str = "",
    mime_type: str = "",
    account: str = "",
) -> str:
    """Create or update a Drive file. Omit `file_id` to create; pass it
    (from google_drive_search) to overwrite. Default mime is text/plain.
    This writes immediately."""
    return _run(
        account or None,
        lambda token: drive_write(
            token,
            name=name,
            content=content,
            file_id=file_id or None,
            mime_type=mime_type or None,
        ),
        SCOPE_DRIVE,
    )


@tool
def google_docs_read(document_id: str, account: str = "") -> str:
    """Read a Google Doc by id (from google_drive_search). Returns the
    title and extracted text."""
    return _run(account or None, lambda token: docs_read(token, document_id), SCOPE_DOCS_READONLY)


@tool
def google_docs_append(document_id: str, text: str, account: str = "") -> str:
    """Append `text` to the end of a Google Doc. Does not replace
    existing content."""
    return _run(
        account or None,
        lambda token: docs_append(token, document_id, text),
        SCOPE_DOCS,
    )


@tool
def google_sheets_read(spreadsheet_id: str, range_a1: str = "A1:Z100", account: str = "") -> str:
    """Read cells from a Google Sheet. `range_a1` is A1 notation
    (Sheet1!A1:C10). Returns tab-separated rows."""
    return _run(
        account or None,
        lambda token: sheets_read(token, spreadsheet_id, range_a1),
        SCOPE_SHEETS_READONLY,
    )


@tool
def google_sheets_update(spreadsheet_id: str, range_a1: str, values: str, account: str = "") -> str:
    """Write cells to a Google Sheet. `range_a1` is A1 notation. `values`
    is tab-separated rows (newlines between rows) or a JSON array of
    arrays. This writes immediately."""
    return _run(
        account or None,
        lambda token: sheets_update(token, spreadsheet_id, range_a1, values),
        SCOPE_SHEETS,
    )


@tool
def google_contacts_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search the connected Google account's Contacts. `query` is a name,
    email, or phone. Returns contact ids plus name/email/phone."""
    return _run(
        account or None,
        lambda token: contacts_search(token, query, max_results),
        SCOPE_CONTACTS_READONLY,
    )


@tool
def google_tasks_list(
    task_list: str = "",
    max_results: int = 20,
    include_completed: bool = False,
    account: str = "",
) -> str:
    """List Google Tasks. Omit `task_list` to walk every list; pass a
    list id (from a previous call) to stay on one. Incomplete tasks
    only unless `include_completed` is true."""
    return _run(
        account or None,
        lambda token: tasks_list(
            token,
            task_list=task_list or None,
            max_results=max_results,
            include_completed=include_completed,
        ),
        SCOPE_TASKS_READONLY,
    )


@tool
def google_tasks_add(
    title: str,
    notes: str = "",
    due: str = "",
    task_list: str = "",
    account: str = "",
) -> str:
    """Add a Google Task. `due` is YYYY-MM-DD or RFC3339. Omit
    `task_list` to use the default list. This writes immediately."""
    return _run(
        account or None,
        lambda token: tasks_add(
            token,
            title=title,
            notes=notes or None,
            due=due or None,
            task_list=task_list or None,
        ),
        SCOPE_TASKS,
    )


@tool
def google_meet_create(access_type: str = "", account: str = "") -> str:
    """Create a Google Meet space and return the join link.
    `access_type` is OPEN, TRUSTED, or RESTRICTED; omit for Google's
    default. This creates immediately."""
    return _run(
        account or None,
        lambda token: meet_create(token, access_type=access_type or None),
        SCOPE_MEET_SPACE_CREATED,
    )
