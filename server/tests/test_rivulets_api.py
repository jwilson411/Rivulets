"""api/rivulets.py HTTP-layer coverage that test_rivulet_dispatch.py doesn't
already exercise: 404s, list_rivulets, an empty messages list, and the SSE
stream endpoint itself (test_streaming.py only checks the pub/sub broadcaster
and dispatch's publish calls, never the /stream route)."""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets import streaming
from rivulets.api.deps import SessionClaims
from rivulets.api.rivulets import _run_message_dispatch, stream_rivulet
from rivulets.db.models import Channel, Message, Rivulet
from rivulets.db.session import session_scope
from rivulets.tracing import start_trace

_OWNER_CLAIMS = SessionClaims(workspace_id="workspace-1", human_id=None, grant="owner")
_INVITE_CLAIMS = SessionClaims(workspace_id="workspace-1", human_id="human-1", grant="invite")


def test_list_rivulets_returns_404_for_unknown_channel(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/channels/does-not-exist/rivulets", headers=auth_headers)
    assert response.status_code == 404


def test_get_rivulet_returns_404_for_unknown_rivulet(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/rivulets/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_post_message_returns_404_for_unknown_rivulet(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/rivulets/does-not-exist/messages", json={"content": "hi"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_resume_and_close_return_404_for_unknown_rivulet(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.post("/api/v1/rivulets/nope/resume", headers=auth_headers).status_code == 404
    assert client.delete("/api/v1/rivulets/nope", headers=auth_headers).status_code == 404


def test_list_rivulets_returns_created_rivulets_newest_first(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel = client.post("/api/v1/channels", json={"name": "listing"}, headers=auth_headers)
    channel_id = channel.json()["id"]

    first = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": "first"}, headers=auth_headers
    )
    second = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": "second"}, headers=auth_headers
    )
    assert first.status_code == 201
    assert second.status_code == 201

    listed = client.get(f"/api/v1/channels/{channel_id}/rivulets", headers=auth_headers)
    assert listed.status_code == 200
    ids = [r["id"] for r in listed.json()]
    assert ids == [second.json()["id"], first.json()["id"]]


def test_rivulet_working_directory_overrides_channel(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    river = tmp_path / "river"
    stream = tmp_path / "stream"
    river.mkdir()
    stream.mkdir()
    channel = client.post("/api/v1/channels", json={"name": "wd-inherit"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    client.patch(
        f"/api/v1/channels/{channel_id}",
        json={"working_directory": str(river)},
        headers=auth_headers,
    )
    created = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "start"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["working_directory"] is None
    assert created.json()["effective_working_directory"] == str(river.resolve())

    rivulet_id = created.json()["id"]
    overridden = client.patch(
        f"/api/v1/rivulets/{rivulet_id}",
        json={"working_directory": str(stream)},
        headers=auth_headers,
    )
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["working_directory"] == str(stream.resolve())
    assert overridden.json()["effective_working_directory"] == str(stream.resolve())

    channel_after = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert channel_after["working_directory"] == str(river.resolve())

    cleared = client.patch(
        f"/api/v1/rivulets/{rivulet_id}",
        json={"working_directory": None},
        headers=auth_headers,
    )
    assert cleared.json()["working_directory"] is None
    assert cleared.json()["effective_working_directory"] == str(river.resolve())


def test_rivulet_working_directory_is_owner_only(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    from tests.test_channels import _invite_headers

    project = tmp_path / "guest-cannot"
    project.mkdir()
    channel = client.post("/api/v1/channels", json={"name": "wd-guest"}, headers=auth_headers)
    created = client.post(
        f"/api/v1/channels/{channel.json()['id']}/rivulets",
        json={"content": "start"},
        headers=auth_headers,
    )
    guest = _invite_headers(client, auth_headers)
    response = client.patch(
        f"/api/v1/rivulets/{created.json()['id']}",
        json={"working_directory": str(project)},
        headers=guest,
    )
    assert response.status_code == 403


def test_close_archives_a_rivulet_without_destroying_it(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#412: DELETE is a soft archive (status=closed). The row stays so
    the UI can list it under Archived and resume can reopen it."""
    channel = client.post("/api/v1/channels", json={"name": "archive-me"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    created = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "accidental send"},
        headers=auth_headers,
    )
    rivulet_id = created.json()["id"]

    closed = client.delete(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers)
    assert closed.status_code == 204

    fetched = client.get(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "closed"

    listed = client.get(f"/api/v1/channels/{channel_id}/rivulets", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == rivulet_id
    assert listed.json()[0]["status"] == "closed"

    refused = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "should not land"},
        headers=auth_headers,
    )
    assert refused.status_code == 400
    assert "archived" in refused.json()["detail"]

    reopened = client.post(f"/api/v1/rivulets/{rivulet_id}/resume", headers=auth_headers)
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "active"

    reply = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "now it lands"},
        headers=auth_headers,
    )
    assert reply.status_code == 201


def test_list_messages_on_a_rivulet_with_no_messages_yet_is_empty(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Regression coverage for _attachments_by_message's empty-input
    shortcut -- there's no human root message to fetch attachments for
    until dispatch actually runs, exercised here by reading straight from
    a freshly-created rivulet's own message list before that happens."""
    channel = client.post("/api/v1/channels", json={"name": "empty-msgs"}, headers=auth_headers)
    channel_id = channel.json()["id"]
    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets", json={"content": "root"}, headers=auth_headers
    )
    rivulet_id = rivulet.json()["id"]

    # Delete the auto-created root message directly so the messages list is
    # genuinely empty -- there's no HTTP delete-message endpoint, so this
    # goes straight at the DB the same way other tests reach into it.
    async def _clear() -> None:
        async with session_scope() as db:
            await db.execute(delete(Message).where(Message.rivulet_id == rivulet_id))
            await db.commit()

    asyncio.run(_clear())

    messages = client.get(f"/api/v1/rivulets/{rivulet_id}/messages", headers=auth_headers)
    assert messages.status_code == 200
    assert messages.json() == []


class _FakeStreamRequest:
    """Reports disconnected only after the first check -- lets the SSE
    generator process exactly one already-queued event before exiting."""

    def __init__(self) -> None:
        self._checks = 0

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > 1


async def _call_stream_rivulet(
    rivulet_id: str, request: _FakeStreamRequest, db_session: AsyncSession, claims: SessionClaims
) -> StreamingResponse:
    return await stream_rivulet(rivulet_id, request, db_session, claims)  # type: ignore[arg-type]


async def test_stream_rivulet_yields_a_published_event_then_exits_on_disconnect(
    db_session: AsyncSession,
) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    db_session.add(Rivulet(id="riv-1", channel_id="chan-1", created_by="human", status="active"))
    await db_session.commit()

    request = _FakeStreamRequest()
    response = await _call_stream_rivulet("riv-1", request, db_session, _OWNER_CLAIMS)

    # subscribe() already ran synchronously inside stream_rivulet, before the
    # generator itself starts -- publishing now lands in the same queue the
    # generator will read from once we start iterating it below.
    streaming.publish("riv-1", "agent_token", {"token": "hi"})

    chunks: list[bytes] = [chunk async for chunk in response.body_iterator]  # type: ignore[union-attr]

    assert len(chunks) == 1
    text = chunks[0].decode()
    assert "event: agent_token" in text
    assert '"token": "hi"' in text


async def test_stream_rivulet_returns_404_for_unknown_rivulet(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _call_stream_rivulet(
            "does-not-exist", _FakeStreamRequest(), db_session, _OWNER_CLAIMS
        )
    assert exc_info.value.status_code == 404


async def test_stream_rivulet_owner_only_event_reaches_an_owner_session(
    db_session: AsyncSession,
) -> None:
    """#286: publish()'s `owner_only=True` (used for a freshly created
    invite's one-shot URL) must still reach a genuine owner session's
    stream -- the filtering in streaming.py is per-subscriber grant, not a
    blanket drop."""
    db_session.add(Channel(id="chan-owner-only", name="general"))
    db_session.add(
        Rivulet(
            id="riv-owner-only", channel_id="chan-owner-only", created_by="human", status="active"
        )
    )
    await db_session.commit()

    request = _FakeStreamRequest()
    response = await _call_stream_rivulet("riv-owner-only", request, db_session, _OWNER_CLAIMS)

    streaming.publish(
        "riv-owner-only",
        "system_alert",
        {"type": "invite_created", "url": "http://127.0.0.1:1234/invite/abc.secret"},
        owner_only=True,
    )

    chunks: list[bytes] = [chunk async for chunk in response.body_iterator]  # type: ignore[union-attr]

    assert len(chunks) == 1
    assert "abc.secret" in chunks[0].decode()


async def test_stream_rivulet_owner_only_event_never_reaches_an_invite_grant_session(
    db_session: AsyncSession,
) -> None:
    """#286: an invite-grant EventSource on the rivulet must not observe
    the raw invite secret's URL -- create_invite's `system_alert` payload
    is published with `owner_only=True` specifically so a watching invitee
    can't harvest the next owner-class invite link."""
    db_session.add(Channel(id="chan-invite-grant", name="general"))
    db_session.add(
        Rivulet(
            id="riv-invite-grant",
            channel_id="chan-invite-grant",
            created_by="human",
            status="active",
        )
    )
    await db_session.commit()

    request = _FakeStreamRequest()
    response = await _call_stream_rivulet("riv-invite-grant", request, db_session, _INVITE_CLAIMS)

    streaming.publish(
        "riv-invite-grant",
        "system_alert",
        {"type": "invite_created", "url": "http://127.0.0.1:1234/invite/abc.secret"},
        owner_only=True,
    )
    # A non-owner_only event still must come through -- the invite-grant
    # session is a legitimate subscriber of everything else on this stream.
    streaming.publish("riv-invite-grant", "agent_token", {"token": "hi"})

    chunks: list[bytes] = [chunk async for chunk in response.body_iterator]  # type: ignore[union-attr]

    assert len(chunks) == 1
    text = chunks[0].decode()
    assert "event: agent_token" in text
    assert "abc.secret" not in text


async def test_background_dispatch_crash_posts_a_system_alert(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#413: a failure after the 201 can't become an HTTP error, so the
    thinking row is replaced by a persisted system_alert instead."""
    db_session.add(Channel(id="chan-bg", name="general"))
    db_session.add(Rivulet(id="riv-bg", channel_id="chan-bg", created_by="human", status="active"))
    await db_session.commit()
    trace = await start_trace(
        db_session,
        trigger_type="message",
        label="ping",
        rivulet_id="riv-bg",
        channel_id="chan-bg",
    )
    await db_session.commit()

    async def _boom(*_args: object, **_kwargs: object) -> list[Message]:
        raise RuntimeError("dispatch exploded")

    monkeypatch.setattr("rivulets.api.rivulets.dispatch_and_respond", _boom)

    queue = streaming.subscribe("riv-bg", is_owner=True)
    try:
        await _run_message_dispatch("riv-bg", "chan-bg", "msg-bg", "ping", [], trace.trace_id)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        streaming.unsubscribe("riv-bg", queue)

    assert any(e["event"] == "system_alert" for e in events)
    result = await db_session.execute(select(Message).where(Message.rivulet_id == "riv-bg"))
    alerts = [m for m in result.scalars().all() if m.content_type == "system_alert"]
    assert len(alerts) == 1
    assert "routing that message" in alerts[0].content
