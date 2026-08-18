"""Google Gmail/Calendar builtin tools (tools/builtin/google.py, #458)."""

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
    google_gmail_read,
    google_gmail_search,
    google_gmail_send,
)

google_mod = __import__("rivulets.integrations.google", fromlist=["httpx"])

assert google_gmail_search.entrypoint is not None
assert google_gmail_read.entrypoint is not None
assert google_gmail_send.entrypoint is not None
assert google_calendar_list.entrypoint is not None
assert google_calendar_create.entrypoint is not None

_search = cast("Callable[..., str]", google_gmail_search.entrypoint)
_read = cast("Callable[..., str]", google_gmail_read.entrypoint)
_send = cast("Callable[..., str]", google_gmail_send.entrypoint)
_cal_list = cast("Callable[..., str]", google_calendar_list.entrypoint)
_cal_create = cast("Callable[..., str]", google_calendar_create.entrypoint)

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
