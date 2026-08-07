"""api/rivulets.py HTTP-layer coverage that test_rivulet_dispatch.py doesn't
already exercise: 404s, list_rivulets, an empty messages list, and the SSE
stream endpoint itself (test_streaming.py only checks the pub/sub broadcaster
and dispatch's publish calls, never the /stream route)."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets import streaming
from rivulets.api.rivulets import stream_rivulet
from rivulets.db.models import Channel, Message, Rivulet
from rivulets.db.session import session_scope


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


async def test_stream_rivulet_yields_a_published_event_then_exits_on_disconnect(
    db_session: AsyncSession,
) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    db_session.add(Rivulet(id="riv-1", channel_id="chan-1", created_by="human", status="active"))
    await db_session.commit()

    request = _FakeStreamRequest()
    response = await stream_rivulet("riv-1", request, db_session, "workspace-1")  # type: ignore[arg-type]

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
        await stream_rivulet(
            "does-not-exist",
            _FakeStreamRequest(),  # type: ignore[arg-type]
            db_session,
            "workspace-1",
        )
    assert exc_info.value.status_code == 404
