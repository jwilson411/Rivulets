"""Unit coverage for integrations/google.py error branches and helpers (#458).

test_google_tools.py drives the happy paths through the Agno tool
entrypoints; these tests hit the module functions directly so the
error-handling branches (Google 4xx replies, malformed payloads, expired
tokens) are exercised without a connected account where possible.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import IntegrationAccount, IntegrationOAuthApp
from rivulets.integrations import google as g
from rivulets.integrations.registry import (
    ConnectedAccount,
    account_from_row,
    get_connected_account,
    list_connected_accounts,
    load_integration_registry,
    reset_integration_registry_for_testing,
    upsert_connected_account,
)
from rivulets.integrations.tokens import (
    StoredTokens,
    load_tokens,
    prefer_existing_refresh,
    store_tokens,
)
from rivulets.security.credentials import CredentialStoreError

_RealClient = httpx.Client


def _mock_client_factory(handler: Any) -> Any:
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler), **kwargs
    )


@pytest.fixture(autouse=True)
def _clean_module_state() -> Any:  # pyright: ignore[reportUnusedFunction]
    reset_integration_registry_for_testing()
    g.reset_pending_oauth_for_testing()
    g.reset_oauth_client_cache_for_testing()
    yield
    reset_integration_registry_for_testing()
    g.reset_pending_oauth_for_testing()
    g.reset_oauth_client_cache_for_testing()


def _use_handler(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    monkeypatch.setattr(g.httpx, "Client", _mock_client_factory(handler))


# --- OAuth flow -----------------------------------------------------------


def test_start_authorization_purges_expired_pending() -> None:
    g._pending["stale"] = g.PendingOAuth(
        label="Old", code_verifier="v", created_at=time.monotonic() - 700
    )
    url = g.start_authorization("Work", "client-1", login_hint="ada@example.com")
    assert "stale" not in g._pending
    assert "login_hint=ada%40example.com" in url
    assert url.startswith(g.AUTH_ENDPOINT)


def test_take_pending_rejects_expired_state() -> None:
    g._pending["old"] = g.PendingOAuth(
        label="Old", code_verifier="v", created_at=time.monotonic() - 700
    )
    with pytest.raises(g.GoogleIntegrationError, match="invalid or has expired"):
        g.take_pending("old")


def test_take_pending_rejects_unknown_state() -> None:
    with pytest.raises(g.GoogleIntegrationError, match="invalid or has expired"):
        g.take_pending("never-issued")


def test_exchange_code_sends_client_secret_and_raises_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(400, json={"error_description": "bad code"})

    _use_handler(monkeypatch, handler)
    with pytest.raises(g.GoogleIntegrationError, match="bad code"):
        g.exchange_code(
            code="c",
            code_verifier="v",
            client_id="id",
            client_secret="shh",  # noqa: S106
        )
    assert "client_secret=shh" in seen["body"]


def test_exchange_code_returns_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "access_token": "ya29.new",
                "refresh_token": "1//r",
                "expires_in": 3600,
                "scope": "email openid",
                "token_type": "Bearer",
            },
        )

    _use_handler(monkeypatch, handler)
    tokens = g.exchange_code(code="c", code_verifier="v", client_id="id", client_secret=None)
    assert tokens.access_token == "ya29.new"  # noqa: S105
    assert tokens.refresh_token == "1//r"  # noqa: S105
    assert tokens.scopes == ("email", "openid")
    assert tokens.expiry is not None


def test_refresh_tokens_requires_refresh_token() -> None:
    bare = StoredTokens(access_token="a", refresh_token=None, expiry=None, scopes=())  # noqa: S106
    with pytest.raises(g.GoogleAuthExpiredError, match="no refresh token"):
        g.refresh_tokens(bare, client_id="id", client_secret=None)


def test_refresh_tokens_raises_auth_expired_on_google_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(400, json={"error": "invalid_grant"})

    _use_handler(monkeypatch, handler)
    tokens = StoredTokens(access_token="a", refresh_token="r", expiry=None, scopes=())  # noqa: S106
    with pytest.raises(g.GoogleAuthExpiredError, match="invalid_grant"):
        g.refresh_tokens(tokens, client_id="id", client_secret="shh")  # noqa: S106


def test_refresh_tokens_keeps_old_refresh_token_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "ya29.fresh"})

    _use_handler(monkeypatch, handler)
    old = StoredTokens(
        access_token="stale",  # noqa: S106
        refresh_token="keep-me",  # noqa: S106
        expiry=None,
        scopes=("email",),
    )
    refreshed = g.refresh_tokens(old, client_id="id", client_secret=None)
    assert refreshed.access_token == "ya29.fresh"  # noqa: S105
    assert refreshed.refresh_token == "keep-me"  # noqa: S105
    assert refreshed.scopes == ("email",)


def test_fetch_account_email_handles_error_and_bad_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(401, text="nope"))
    assert g.fetch_account_email("tok") is None

    _use_handler(monkeypatch, lambda request: httpx.Response(200, json=["not", "a", "dict"]))
    assert g.fetch_account_email("tok") is None

    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={"email": "a@b.c"}))
    assert g.fetch_account_email("tok") == "a@b.c"


def test_revoke_token_swallows_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    _use_handler(monkeypatch, handler)
    g.revoke_token("tok")  # must not raise


def test_tokens_from_response_rejects_bad_payloads() -> None:
    with pytest.raises(g.GoogleIntegrationError, match="unexpected token response"):
        g._tokens_from_response(["nope"])
    with pytest.raises(g.GoogleIntegrationError, match="did not return an access token"):
        g._tokens_from_response({"token_type": "Bearer"})


def test_google_error_message_branches() -> None:
    assert g._google_error_message(httpx.Response(500, text="<html>"), "fallback") == "fallback"
    assert g._google_error_message(httpx.Response(400, json=[1]), "fallback") == "fallback"
    assert g._google_error_message(httpx.Response(400, json={"error": "plain"}), "f") == "plain"
    nested = httpx.Response(400, json={"error": {"message": "nested msg"}})
    assert g._google_error_message(nested, "f") == "nested msg"
    blank = httpx.Response(400, json={"error": {"message": "  "}})
    assert g._google_error_message(blank, "fallback") == "fallback"
    scoped = httpx.Response(403, json={"error_description": "insufficient scope granted"})
    assert "Settings → Integrations" in g._google_error_message(scoped, "f")


def _connect(*, expired: bool = False) -> None:
    store_tokens(
        "acct-1",
        StoredTokens(
            access_token="ya29.live",  # noqa: S106
            refresh_token="1//refresh",  # noqa: S106
            expiry=datetime.now(UTC) + (timedelta(hours=-1) if expired else timedelta(hours=1)),
            scopes=g.CONNECT_SCOPES,
        ),
    )
    upsert_connected_account(
        ConnectedAccount(
            id="acct-1",
            provider="google",
            label="Work",
            account_email="ada@example.com",
            status="connected",
            scopes=g.CONNECT_SCOPES,
            credential_ref="integration:acct-1",
        )
    )


def test_resolve_access_token_reports_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert_connected_account(
        ConnectedAccount(
            id="acct-lost",
            provider="google",
            label="Lost",
            account_email=None,
            status="connected",
            scopes=(),
            credential_ref="integration:acct-lost",
        )
    )

    def missing(_ref: str) -> StoredTokens:
        raise CredentialStoreError("gone")

    monkeypatch.setattr(g, "load_tokens", missing)
    with pytest.raises(g.GoogleAuthExpiredError, match="credentials are missing"):
        g.resolve_access_token(account=None, client_id="id", client_secret=None)


def test_resolve_access_token_refreshes_expired_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect(expired=True)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"access_token": "ya29.refreshed", "expires_in": 3600})

    _use_handler(monkeypatch, handler)
    connected, access = g.resolve_access_token(account=None, client_id="id", client_secret=None)
    assert connected.id == "acct-1"
    assert access == "ya29.refreshed"
    # The refreshed token must be persisted, not just used once.
    assert load_tokens("integration:acct-1").access_token == "ya29.refreshed"  # noqa: S105


# --- Gmail ----------------------------------------------------------------


def test_gmail_search_error_empty_and_partial_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(403, json={"error": "denied"}))
    assert g.gmail_search("tok", "q", 5) == "denied"

    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert g.gmail_search("tok", "q", 5) == "No messages matched."

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"messages": ["not-a-dict", {"noid": True}, {"id": "m9"}]}
            )
        return httpx.Response(500, json={"error": "meta down"})

    _use_handler(monkeypatch, handler)
    result = g.gmail_search("tok", "q", 5)
    assert "m9" in result
    assert "couldn't load headers" in result


def test_gmail_read_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(404, json={"error": "gone"}))
    assert g.gmail_read("tok", "m1") == "gone"


def test_gmail_draft_success_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/drafts")
        return httpx.Response(200, json={"id": "d1", "message": {"id": "m1"}})

    _use_handler(monkeypatch, handler)
    result = g.gmail_draft("tok", "bob@example.com", "Hi", "Body")
    assert "draft_id=d1" in result
    assert "message_id=m1" in result

    _use_handler(monkeypatch, lambda request: httpx.Response(403, json={"error": "no"}))
    assert g.gmail_draft("tok", "bob@example.com", "Hi", "Body") == "no"


def test_gmail_send_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(400, json={"error": "bad"}))
    assert g.gmail_send("tok", "b@c.d", "s", "b") == "bad"


# --- Calendar -------------------------------------------------------------


def test_calendar_list_time_max_error_empty_and_bad_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeMax"] = request.url.params.get("timeMax", "")
        return httpx.Response(200, json={"items": ["junk"]})

    _use_handler(monkeypatch, handler)
    # The only item is junk, so every row is skipped and nothing renders.
    listed = g.calendar_list("tok", time_min=None, time_max="2026-09-01T00:00:00Z", max_results=5)
    assert listed == ""
    assert seen["timeMax"] == "2026-09-01T00:00:00Z"

    _use_handler(monkeypatch, lambda request: httpx.Response(500, json={"error": "down"}))
    assert g.calendar_list("tok", time_min=None, time_max=None, max_results=5) == "down"

    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={"items": []}))
    assert (
        g.calendar_list("tok", time_min=None, time_max=None, max_results=5) == "No upcoming events."
    )


def test_calendar_create_with_description_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "ev1", "htmlLink": "https://cal"})

    _use_handler(monkeypatch, handler)
    result = g.calendar_create(
        "tok", summary="Sync", start="2026-08-19", end="2026-08-20", description="  notes  "
    )
    assert "ev1" in result
    assert '"description":"notes"' in captured["body"]

    _use_handler(monkeypatch, lambda request: httpx.Response(409, json={"error": "clash"}))
    assert g.calendar_create("tok", summary="S", start="a", end="b", description=None) == "clash"


def test_calendar_update_builds_partial_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "ev1"})

    _use_handler(monkeypatch, handler)
    result = g.calendar_update(
        "tok",
        "ev1",
        summary=None,
        start="2026-08-19T10:00:00Z",
        end="2026-08-19T11:00:00Z",
        description="moved",
    )
    assert "Event updated" in result
    assert "dateTime" in captured["body"]
    assert "moved" in captured["body"]


# --- Drive ----------------------------------------------------------------


def test_drive_search_skips_bad_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200, json={"files": ["junk", {"id": "f1", "name": "Notes", "mimeType": "text/plain"}]}
        )

    _use_handler(monkeypatch, handler)
    result = g.drive_search("tok", "name contains 'Notes'", 5)
    assert "f1" in result
    assert "junk" not in result


def test_drive_read_reports_export_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/export" in request.url.path:
            return httpx.Response(403, json={"error": "export blocked"})
        return httpx.Response(
            200,
            json={
                "id": "f1",
                "name": "Doc",
                "mimeType": g._GOOGLE_DOC,
                "modifiedTime": "2026-08-18T00:00:00Z",
                "webViewLink": "https://drive/f1",
                "description": "shared notes",
            },
        )

    _use_handler(monkeypatch, handler)
    result = g.drive_read("tok", "f1")
    assert "export blocked" in result
    assert "modified: 2026-08-18T00:00:00Z" in result
    assert "shared notes" in result


def test_drive_read_reports_download_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            return httpx.Response(500, json={"error": "media down"})
        return httpx.Response(200, json={"id": "f1", "name": "a.txt", "mimeType": "text/plain"})

    _use_handler(monkeypatch, handler)
    assert "media down" in g.drive_read("tok", "f1")


def test_drive_write_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(403, json={"error": "rename no"}))
    assert g.drive_write("tok", name="New", content="", file_id="f1", mime_type=None) == "rename no"

    def content_fails(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and "/upload/" in request.url.path:
            return httpx.Response(500, json={"error": "upload no"})
        return httpx.Response(200, json={"id": "f1"})

    _use_handler(monkeypatch, content_fails)
    assert (
        g.drive_write("tok", name="", content="data", file_id="f1", mime_type=None) == "upload no"
    )

    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert (
        g.drive_write("tok", name="New", content="", file_id=None, mime_type=None)
        == "Couldn't create the Drive file."
    )

    # Create succeeds, the follow-up content upload fails: Google's own
    # error text wins over the "couldn't write its content" fallback.
    _use_handler(monkeypatch, content_fails)
    result = g.drive_write("tok", name="New", content="data", file_id=None, mime_type=None)
    assert result == "upload no"


# --- Docs / Sheets --------------------------------------------------------


def test_docs_append_surfaces_batch_update_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":batchUpdate"):
            return httpx.Response(400, json={"error": "insert no"})
        return httpx.Response(200, json={"title": "Doc", "body": {"content": [{"endIndex": 12}]}})

    _use_handler(monkeypatch, handler)
    assert g.docs_append("tok", "doc1", "more") == "insert no"


def test_sheet_values_parsing_fallbacks() -> None:
    # Broken JSON falls back to tab-separated lines.
    assert g._sheet_values("[not json\ta") == [["[not json", "a"]]
    # A JSON array whose rows aren't arrays wraps each scalar as one cell.
    assert g._sheet_values('["a", ["b", "c"]]') == [["a"], ["b", "c"]]
    assert g._sheet_values("   ") == []


# --- Contacts / Tasks / Meet ---------------------------------------------


def test_contacts_search_fallback_error_and_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    def other_fails(request: httpx.Request) -> httpx.Response:
        if "otherContacts" in request.url.path:
            return httpx.Response(500, json={"error": "other down"})
        return httpx.Response(200, json={"results": []})

    _use_handler(monkeypatch, other_fails)
    assert g.contacts_search("tok", "ada", 5) == "other down"

    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={"results": []}))
    assert g.contacts_search("tok", "ada", 5) == "No contacts matched."


def test_tasks_list_specific_list_includes_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["showCompleted"] = request.url.params.get("showCompleted", "")
        seen["showHidden"] = request.url.params.get("showHidden", "")
        return httpx.Response(
            200,
            json={
                "items": [
                    "junk",
                    {"id": "t1", "title": "Ship it", "status": "needsAction", "notes": "soon"},
                ]
            },
        )

    _use_handler(monkeypatch, handler)
    result = g.tasks_list("tok", task_list="lid-1", max_results=10, include_completed=True)
    assert seen["showCompleted"] == "true"
    assert seen["showHidden"] == "true"
    assert "Ship it" in result
    assert "soon" in result


def test_tasks_list_handles_empty_and_bad_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={"items": []}))
    assert g.tasks_list("tok", task_list=None, max_results=10, include_completed=False) == (
        "No task lists."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lists"):
            return httpx.Response(
                200, json={"items": ["junk", {"title": "No id"}, {"id": "lid", "title": "Home"}]}
            )
        return httpx.Response(200, json={"items": []})

    _use_handler(monkeypatch, handler)
    result = g.tasks_list("tok", task_list=None, max_results=10, include_completed=False)
    assert "Home" in result
    assert "No tasks." in result


def test_tasks_for_list_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(500, json={"error": "tasks down"}))
    assert (
        g.tasks_list("tok", task_list="lid", max_results=5, include_completed=False) == "tasks down"
    )


def test_tasks_add_sends_notes_and_datetime_due(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "t1"})

    _use_handler(monkeypatch, handler)
    result = g.tasks_add(
        "tok",
        title="Ship",
        notes="tonight",
        due="2026-08-19T12:00:00Z",
        task_list=None,
    )
    assert "task_id=t1" in result
    assert "tonight" in captured["body"]
    assert "2026-08-19T12:00:00Z" in captured["body"]


def test_meet_create_handles_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_handler(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert g.meet_create("tok", access_type=None) == "Couldn't create a Meet link."


# --- Pure helpers ---------------------------------------------------------


def test_event_time_and_body_time_helpers() -> None:
    assert g._event_time("not-a-dict") == ""
    assert g._event_time({"date": "2026-08-19"}) == "2026-08-19"
    assert g._event_body_time("2026-08-19") == {"date": "2026-08-19"}
    assert g._event_body_time("2026-08-19T10:00:00Z") == {"dateTime": "2026-08-19T10:00:00Z"}


def test_gmail_headers_skips_bad_rows() -> None:
    payload = {"headers": ["junk", {"name": "From", "value": "Ada"}]}
    assert g._gmail_headers(payload) == {"From": "Ada"}


def test_gmail_body_multipart_and_fallbacks() -> None:
    assert g._gmail_body(None) == ""
    # "SGVsbG8=" is "Hello", "PGI+aGk8L2I+" is "<b>hi</b>".
    multipart = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": "PGI-aGk8L2I-"}},
            {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
        ],
    }
    assert g._gmail_body(multipart) == "Hello"
    html_only = {
        "mimeType": "multipart/alternative",
        "parts": [{"mimeType": "text/html", "body": {"data": "PGI-aGk8L2I-"}}],
    }
    assert g._gmail_body(html_only) == "<b>hi</b>"
    untyped_part = {
        "mimeType": "multipart/mixed",
        "parts": [{"body": {"data": "SGVsbG8="}}],
    }
    assert g._gmail_body(untyped_part) == "Hello"
    top_level = {"mimeType": "application/octet-stream", "body": {"data": "SGVsbG8="}}
    assert g._gmail_body(top_level) == "Hello"


def test_b64url_decode_and_clip() -> None:
    assert g._b64url_decode("!!!not base64!!!") == ""
    long = "x" * (g._MAX_READ_CHARS + 10)
    clipped = g._clip(long)
    assert clipped.endswith("…(truncated)")
    assert g._clip("short") == "short"


def test_drive_export_mime_for_sheets() -> None:
    assert g._drive_export_mime(g._GOOGLE_SHEET) == "text/csv"
    assert g._drive_export_mime(g._GOOGLE_DOC) == "text/plain"
    assert g._drive_export_mime("image/png") is None


def test_docs_structural_text_walks_toc_and_skips_junk() -> None:
    content = [
        "junk",
        {
            "tableOfContents": {
                "content": [{"paragraph": {"elements": [{"textRun": {"content": "TOC entry"}}]}}]
            }
        },
    ]
    assert g._docs_structural_text(content) == "TOC entry"


def test_contact_helpers_skip_bad_rows() -> None:
    assert g._contact_results({"results": ["junk"]}) == []
    assert g._first_named(["junk", {"displayName": "Ada"}], "displayName") == "Ada"
    assert g._joined_values(["junk", {"value": "a@b.c"}, {"value": "d@e.f"}]) == "a@b.c, d@e.f"


def test_task_due_passthrough_for_datetime() -> None:
    assert g._task_due("2026-08-19T12:00:00Z") == "2026-08-19T12:00:00Z"
    assert g._task_due("2026-08-19") == "2026-08-19T00:00:00.000Z"


def test_load_client_secret_missing_ref_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert g.load_client_secret(None) is None

    def missing(_ref: str) -> str:
        raise CredentialStoreError("gone")

    monkeypatch.setattr(g, "get_secret", missing)
    assert g.load_client_secret("integration:never-stored") is None


def test_gmail_body_skips_empty_parts_and_defaults_blank() -> None:
    empty_part = {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "text/plain", "body": {}}],
    }
    assert g._gmail_body(empty_part) == ""
    assert g._gmail_body({"mimeType": "application/octet-stream", "body": {}}) == ""


# --- tools/builtin/google.py ---------------------------------------------


def test_gmail_draft_tool_reports_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import rivulets.tools.builtin.google as tools_mod

    assert tools_mod.google_gmail_draft.entrypoint is not None
    draft = cast("Callable[..., str]", tools_mod.google_gmail_draft.entrypoint)

    _connect(expired=True)
    g.cache_oauth_client("client-123", None)
    # Refresh fails, so _run surfaces the GoogleAuthExpiredError message.
    _use_handler(monkeypatch, lambda request: httpx.Response(400, json={"error": "expired"}))
    assert "expired" in draft(to="b@c.d", subject="Hi", body="Body")

    def boom(**_kwargs: object) -> object:
        raise g.GoogleIntegrationError("integration broke")

    monkeypatch.setattr(tools_mod, "resolve_access_token", boom)
    assert draft(to="b@c.d", subject="Hi", body="Body") == "integration broke"


def test_gmail_draft_tool_creates_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    import rivulets.tools.builtin.google as tools_mod

    draft = cast("Callable[..., str]", tools_mod.google_gmail_draft.entrypoint)
    _connect()
    g.cache_oauth_client("client-123", None)
    _use_handler(
        monkeypatch,
        lambda request: httpx.Response(200, json={"id": "d1", "message": {"id": "m1"}}),
    )
    assert "draft_id=d1" in draft(to="b@c.d", subject="Hi", body="Body")


# --- tokens.py ------------------------------------------------------------


def test_stored_tokens_without_expiry_never_expire() -> None:
    assert not StoredTokens(access_token="a", refresh_token=None, expiry=None, scopes=()).expired()  # noqa: S106


def test_prefer_existing_refresh_without_existing() -> None:
    new = StoredTokens(access_token="a", refresh_token=None, expiry=None, scopes=())  # noqa: S106
    assert prefer_existing_refresh(new, None) is new


def test_load_tokens_rejects_corrupt_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    import rivulets.integrations.tokens as tokens_mod

    stored = {"ref": "{not json"}

    def fake_get_secret(_ref: str) -> str:
        return stored["ref"]

    monkeypatch.setattr(tokens_mod, "get_secret", fake_get_secret)
    with pytest.raises(CredentialStoreError, match="Corrupt token payload"):
        load_tokens("integration:x")

    stored["ref"] = '["not", "a", "dict"]'
    with pytest.raises(CredentialStoreError, match="Corrupt token payload"):
        load_tokens("integration:x")

    stored["ref"] = '{"refresh_token": "r"}'
    with pytest.raises(CredentialStoreError, match="No access token stored"):
        load_tokens("integration:x")


def test_load_tokens_normalizes_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    import rivulets.integrations.tokens as tokens_mod

    stored = {"ref": '{"access_token": "a", "expiry": "not-a-date"}'}

    def fake_get_secret(_ref: str) -> str:
        return stored["ref"]

    monkeypatch.setattr(tokens_mod, "get_secret", fake_get_secret)
    assert load_tokens("integration:x").expiry is None

    stored["ref"] = '{"access_token": "a", "expiry": "2026-08-19T12:00:00"}'
    naive = load_tokens("integration:x").expiry
    assert naive is not None
    assert naive.tzinfo is UTC


# --- registry.py ----------------------------------------------------------


def test_get_connected_account_matches_id_email_and_label() -> None:
    row = ConnectedAccount(
        id="acct-9",
        provider="google",
        label="Work",
        account_email="ada@example.com",
        status="connected",
        scopes=(),
        credential_ref="integration:acct-9",
    )
    upsert_connected_account(row)
    assert get_connected_account("google", "ACCT-9") is row
    assert get_connected_account("google", "Ada@Example.com") is row
    assert get_connected_account("google", "work") is row
    assert get_connected_account("google", "nobody") is None
    assert get_connected_account("google", "  ") is row


def test_account_from_row_filters_and_parses() -> None:
    disconnected = IntegrationAccount(
        id="a1",
        provider="google",
        label="Old",
        status="error",
        scopes_json="[]",
        credential_ref="integration:a1",
    )
    assert account_from_row(disconnected) is None

    bad_json = IntegrationAccount(
        id="a2",
        provider="google",
        label="Work",
        status="connected",
        scopes_json="{not json",
        credential_ref="integration:a2",
    )
    loaded = account_from_row(bad_json)
    assert loaded is not None
    assert loaded.scopes == ()


async def test_load_integration_registry_populates_index_and_client(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        IntegrationAccount(
            id="acct-db",
            provider="google",
            label="Work",
            account_email="ada@example.com",
            status="connected",
            scopes_json='["email"]',
            credential_ref="integration:acct-db",
        )
    )
    db_session.add(
        IntegrationAccount(
            id="acct-dead",
            provider="google",
            label="Dead",
            status="error",
            scopes_json="[]",
            credential_ref="integration:acct-dead",
        )
    )
    db_session.add(IntegrationOAuthApp(provider="google", client_id="client-db"))
    await db_session.commit()

    await load_integration_registry(db_session)
    accounts = list_connected_accounts("google")
    assert [a.id for a in accounts] == ["acct-db"]
    assert accounts[0].scopes == ("email",)
    assert g.cached_oauth_client() == ("client-db", None)
