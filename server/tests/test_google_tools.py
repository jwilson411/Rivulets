"""Google Workspace builtin tools (tools/builtin/google.py, #458)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from rivulets.integrations.google import cache_oauth_client
from rivulets.integrations.registry import ConnectedAccount, upsert_connected_account
from rivulets.integrations.tokens import StoredTokens, store_tokens
from rivulets.tools.builtin.google import (
    google_calendar_create,
    google_calendar_list,
    google_calendar_update,
    google_docs_append,
    google_docs_read,
    google_drive_read,
    google_drive_search,
    google_drive_write,
    google_gmail_read,
    google_gmail_search,
    google_gmail_send,
    google_sheets_read,
    google_sheets_update,
)

google_mod = __import__("rivulets.integrations.google", fromlist=["httpx"])

assert google_gmail_search.entrypoint is not None
assert google_gmail_read.entrypoint is not None
assert google_gmail_send.entrypoint is not None
assert google_calendar_list.entrypoint is not None
assert google_calendar_create.entrypoint is not None
assert google_calendar_update.entrypoint is not None
assert google_drive_search.entrypoint is not None
assert google_drive_read.entrypoint is not None
assert google_drive_write.entrypoint is not None
assert google_docs_read.entrypoint is not None
assert google_docs_append.entrypoint is not None
assert google_sheets_read.entrypoint is not None
assert google_sheets_update.entrypoint is not None

_search = cast("Callable[..., str]", google_gmail_search.entrypoint)
_read = cast("Callable[..., str]", google_gmail_read.entrypoint)
_send = cast("Callable[..., str]", google_gmail_send.entrypoint)
_cal_list = cast("Callable[..., str]", google_calendar_list.entrypoint)
_cal_create = cast("Callable[..., str]", google_calendar_create.entrypoint)
_cal_update = cast("Callable[..., str]", google_calendar_update.entrypoint)
_drive_search = cast("Callable[..., str]", google_drive_search.entrypoint)
_drive_read = cast("Callable[..., str]", google_drive_read.entrypoint)
_drive_write = cast("Callable[..., str]", google_drive_write.entrypoint)
_docs_read = cast("Callable[..., str]", google_docs_read.entrypoint)
_docs_append = cast("Callable[..., str]", google_docs_append.entrypoint)
_sheets_read = cast("Callable[..., str]", google_sheets_read.entrypoint)
_sheets_update = cast("Callable[..., str]", google_sheets_update.entrypoint)

_RealClient = httpx.Client


def _mock_client_factory(handler: Any) -> Any:
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler), **kwargs
    )


def _connect_account() -> None:
    store_tokens(
        "acct-1",
        StoredTokens(
            access_token="ya29.live",  # noqa: S106
            refresh_token="1//refresh",  # noqa: S106
            expiry=datetime.now(UTC) + timedelta(hours=1),
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        ),
    )
    upsert_connected_account(
        ConnectedAccount(
            id="acct-1",
            provider="google",
            label="Work",
            account_email="ada@example.com",
            status="connected",
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
            credential_ref="integration:acct-1",
        )
    )
    cache_oauth_client("client-123", None)


def test_google_tools_fail_closed_when_disconnected() -> None:
    result = _search(query="from:ada")
    assert "No Google account is connected" in result


def test_gmail_search_formats_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages") and request.method == "GET":
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if request.url.path.endswith("/messages/m1"):
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Ada <ada@example.com>"},
                            {"name": "Subject", "value": "Hello"},
                            {"name": "Date", "value": "Mon, 18 Aug 2026"},
                        ]
                    },
                },
            )
        return httpx.Response(404, text=str(request.url))

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))

    result = _search(query="from:ada", max_results=5)
    assert "m1" in result
    assert "Ada <ada@example.com>" in result
    assert "Hello" in result


def test_gmail_read_returns_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "Ada"},
                        {"name": "Subject", "value": "Hi"},
                        {"name": "To", "value": "me@example.com"},
                        {"name": "Date", "value": "today"},
                    ],
                    "body": {
                        "data": "SGVsbG8gd29ybGQ="  # "Hello world"
                    },
                }
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _read(message_id="m1")
    assert "From: Ada" in result
    assert "Hello world" in result


def test_gmail_send_posts_raw_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "sent-1"})

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _send(to="bob@example.com", subject="Ping", body="Are you there?")
    assert "sent-1" in result
    assert "messages/send" in captured["url"]
    assert "raw" in captured["body"]


def test_calendar_list_formats_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ev1",
                        "summary": "Standup",
                        "start": {"dateTime": "2026-08-18T15:00:00Z"},
                        "end": {"dateTime": "2026-08-18T15:15:00Z"},
                        "htmlLink": "https://calendar.google.com/event?eid=ev1",
                    }
                ]
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _cal_list()
    assert "Standup" in result
    assert "ev1" in result
    assert "2026-08-18T15:00:00Z" in result


def test_calendar_create_posts_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200, json={"id": "ev-new", "htmlLink": "https://calendar.google.com/event?eid=n"}
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _cal_create(
        summary="Ship review",
        start="2026-08-19T10:00:00Z",
        end="2026-08-19T11:00:00Z",
    )
    assert "ev-new" in result
    assert "Ship review" in captured["body"]


def test_calendar_update_patches_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200, json={"id": "ev1", "htmlLink": "https://calendar.google.com/event?eid=ev1"}
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _cal_update(event_id="ev1", summary="Renamed standup")
    assert "ev1" in result
    assert captured["method"] == "PATCH"
    assert "/events/ev1" in captured["url"]
    assert "Renamed standup" in captured["body"]


def test_drive_search_formats_files(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/files")
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "file-1",
                        "name": "Notes",
                        "mimeType": "application/vnd.google-apps.document",
                        "modifiedTime": "2026-08-18T12:00:00.000Z",
                        "webViewLink": "https://docs.google.com/document/d/file-1",
                    }
                ]
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_search(query="name contains 'Notes'")
    assert "file-1" in result
    assert "Notes" in result
    assert "application/vnd.google-apps.document" in result


def test_drive_read_exports_google_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/file-1") and "alt=media" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": "file-1",
                    "name": "Notes",
                    "mimeType": "application/vnd.google-apps.document",
                    "webViewLink": "https://docs.google.com/document/d/file-1",
                },
            )
        if request.url.path.endswith("/files/file-1/export"):
            return httpx.Response(200, text="Exported body")
        return httpx.Response(404, text=str(request.url))

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_read(file_id="file-1")
    assert "Notes" in result
    assert "Exported body" in result


def test_drive_write_creates_then_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path.endswith("/files"):
            return httpx.Response(200, json={"id": "new-file"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "new-file"})
        return httpx.Response(404, text=str(request.url))

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_write(name="todo.txt", content="buy milk")
    assert "new-file" in result
    assert any(call.startswith("POST") for call in calls)
    assert any(call.startswith("PATCH") for call in calls)


def test_docs_read_extracts_paragraphs(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "title": "Spec",
                "body": {
                    "content": [
                        {"paragraph": {"elements": [{"textRun": {"content": "Hello "}}]}},
                        {"paragraph": {"elements": [{"textRun": {"content": "world\n"}}]}},
                    ]
                },
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _docs_read(document_id="doc-1")
    assert "Spec" in result
    assert "Hello world" in result


def test_docs_append_inserts_at_end(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "title": "Spec",
                    "body": {"content": [{"endIndex": 12}]},
                },
            )
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"replies": [{}]})

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _docs_append(document_id="doc-1", text="More notes")
    assert "Spec" in result
    assert "batchUpdate" in captured["url"]
    assert "More notes" in captured["body"]
    assert '"index":11' in captured["body"]


def test_sheets_read_formats_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "A1%3AZ100" in str(request.url)
        return httpx.Response(
            200,
            json={"range": "Sheet1!A1:B2", "values": [["Name", "Qty"], ["Apples", "3"]]},
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _sheets_read(spreadsheet_id="sheet-1")
    assert "Sheet1!A1:B2" in result
    assert "Apples\t3" in result


def test_sheets_update_accepts_tsv(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"updatedRange": "Sheet1!A1:B1", "updatedCells": 2})

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _sheets_update(spreadsheet_id="sheet-1", range_a1="A1:B1", values="Hello\tWorld")
    assert "Sheet1!A1:B1" in result
    assert "2 cells" in result
    assert "USER_ENTERED" in captured["url"]
    assert '["Hello","World"]' in captured["body"]


def test_drive_read_skips_binary_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "bin-1",
                "name": "photo.png",
                "mimeType": "image/png",
                "size": "4096",
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_read(file_id="bin-1")
    assert "photo.png" in result
    assert "Binary file" in result


def test_drive_write_updates_existing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"id": "file-1"})

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_write(name="renamed.txt", content="new", file_id="file-1")
    assert "file-1" in result
    assert methods == ["PATCH", "PATCH"]


def test_sheets_update_accepts_json_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"updatedRange": "A1:B1", "updatedCells": 2})

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _sheets_update(
        spreadsheet_id="sheet-1",
        range_a1="A1",
        values='[["Hello","World"]]',
    )
    assert "2 cells" in result
    assert '["Hello","World"]' in captured["body"]


def test_calendar_update_requires_a_field() -> None:
    _connect_account()
    result = _cal_update(event_id="ev1")
    assert "Nothing to update" in result


def test_insufficient_scopes_asks_to_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": "Request had insufficient authentication scopes."}},
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    for result in (
        _drive_search(query="name contains 'x'"),
        _drive_read(file_id="file-1"),
        _docs_read(document_id="doc-1"),
        _docs_append(document_id="doc-1", text="x"),
        _sheets_read(spreadsheet_id="sheet-1"),
        _sheets_update(spreadsheet_id="sheet-1", range_a1="A1", values="x"),
        _cal_update(event_id="ev1", summary="x"),
        _drive_write(name="a.txt", content="hi"),
    ):
        assert "Reconnect" in result


def test_drive_read_downloads_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        if "alt=media" in str(request.url):
            return httpx.Response(200, text="plain body")
        return httpx.Response(
            200,
            json={"id": "t1", "name": "notes.txt", "mimeType": "text/plain", "size": "10"},
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_read(file_id="t1")
    assert "notes.txt" in result
    assert "plain body" in result


def test_drive_read_skips_huge_files(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "big",
                "name": "dump.txt",
                "mimeType": "text/plain",
                "size": "2000000",
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _drive_read(file_id="big")
    assert "too large" in result


def test_drive_search_empty_and_sheets_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/files" in request.url.path:
            return httpx.Response(200, json={"files": []})
        return httpx.Response(200, json={"range": "A1", "values": []})

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    assert "No files matched" in _drive_search(query="name = 'missing'")
    assert "empty" in _sheets_read(spreadsheet_id="sheet-1").lower()


def test_sheets_update_rejects_blank_values() -> None:
    _connect_account()
    assert "No values" in _sheets_update(spreadsheet_id="s", range_a1="A1", values="   ")


def test_docs_read_walks_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect_account()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "title": "Table doc",
                "body": {
                    "content": [
                        {
                            "table": {
                                "tableRows": [
                                    {
                                        "tableCells": [
                                            {
                                                "content": [
                                                    {
                                                        "paragraph": {
                                                            "elements": [
                                                                {"textRun": {"content": "cell"}}
                                                            ]
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    ]
                },
            },
        )

    monkeypatch.setattr(google_mod.httpx, "Client", _mock_client_factory(handler))
    result = _docs_read(document_id="doc-1")
    assert "Table doc" in result
    assert "cell" in result
