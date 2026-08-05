"""Message attachment visibility (FR-10.1's "into threads" -- the read
side). Before this, File.message_id got set on attach but MessageOut had
no way to expose it, so a UI could upload+attach a file but never learn
which messages had attachments, including its own just-created message.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.api.threads import _attach_files  # pyright: ignore[reportPrivateUsage]
from agent_hive.db.models import Channel, File, Message, Thread


def _create_channel(client: TestClient, headers: dict[str, str]) -> str:
    channel = client.post("/api/v1/channels", json={"name": "attachments-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    return channel.json()["id"]


def _upload_file(client: TestClient, headers: dict[str, str], filename: str, content: bytes) -> str:
    upload = client.post(
        "/api/v1/files/upload",
        files={"upload": (filename, content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    return upload.json()["file_id"]


def test_create_thread_message_has_no_attachments_when_none_sent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel_id = _create_channel(client, auth_headers)
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "no files here"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert len(messages) == 1
    assert messages[0]["attachments"] == []


def test_post_message_response_includes_attachment(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel_id = _create_channel(client, auth_headers)
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "starting thread"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    file_id = _upload_file(client, auth_headers, "notes.txt", b"hello attachment")

    posted = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "see attached", "files": [file_id]},
        headers=auth_headers,
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert len(body["attachments"]) == 1
    assert body["attachments"][0] == {
        "file_id": file_id,
        "filename": "notes.txt",
        "mime_type": "text/plain",
        "size_bytes": len(b"hello attachment"),
    }


def test_list_messages_includes_attachments_for_the_root_message(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """create_thread (unlike post_message) doesn't return the message
    itself, only the thread -- attachments on that first message can only
    ever be observed via list_messages, so that path needs its own check."""
    channel_id = _create_channel(client, auth_headers)
    file_id = _upload_file(client, auth_headers, "root.txt", b"root attachment")

    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "root message with a file", "files": [file_id]},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert len(messages) == 1
    assert len(messages[0]["attachments"]) == 1
    assert messages[0]["attachments"][0]["filename"] == "root.txt"


def test_list_messages_only_attributes_files_to_their_own_message(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel_id = _create_channel(client, auth_headers)
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "first message, no file"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    file_id = _upload_file(client, auth_headers, "second-only.txt", b"only on message two")
    client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "second message, with a file", "files": [file_id]},
        headers=auth_headers,
    )

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    assert len(messages) == 2
    assert messages[0]["attachments"] == []
    assert len(messages[1]["attachments"]) == 1
    assert messages[1]["attachments"][0]["filename"] == "second-only.txt"


def test_unknown_file_id_is_ignored_and_message_has_no_attachments(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel_id = _create_channel(client, auth_headers)
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "starting"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]

    posted = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "referencing a bogus file", "files": ["does-not-exist"]},
        headers=auth_headers,
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["attachments"] == []


def test_reattaching_a_file_to_a_different_message_does_not_steal_it(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """A file can only belong to one message (message_id is a single
    column, not a join table). Re-sending its file_id on a second message
    must not silently move it there and empty out the first message's
    attachment list."""
    channel_id = _create_channel(client, auth_headers)
    thread = client.post(
        f"/api/v1/channels/{channel_id}/threads",
        json={"content": "first message"},
        headers=auth_headers,
    )
    thread_id = thread.json()["id"]
    file_id = _upload_file(client, auth_headers, "shared.txt", b"only ever one owner")

    first = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "attaching here first", "files": [file_id]},
        headers=auth_headers,
    )
    assert first.status_code == 201, first.text
    assert len(first.json()["attachments"]) == 1
    first_message_id = first.json()["id"]

    second = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "trying to steal it here", "files": [file_id]},
        headers=auth_headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["attachments"] == []

    messages = client.get(f"/api/v1/threads/{thread_id}/messages", headers=auth_headers).json()
    first_now = next(m for m in messages if m["id"] == first_message_id)
    assert len(first_now["attachments"]) == 1
    assert first_now["attachments"][0]["file_id"] == file_id


async def test_attach_files_is_idempotent_for_the_same_message(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Calling _attach_files twice with the same file_id and the same
    message must not be treated as "already attached elsewhere" -- that
    branch is about a *different* message stealing it, not a retry of the
    same call. Not reachable via the HTTP API today (nothing re-attaches
    to an existing message), so exercised directly."""
    channel = Channel(name="attach-idempotent-test")
    db_session.add(channel)
    await db_session.flush()
    thread = Thread(channel_id=channel.id, created_by="human")
    db_session.add(thread)
    await db_session.flush()
    message = Message(thread_id=thread.id, sender_type="human", sender_name="You", content="hi")
    db_session.add(message)
    await db_session.flush()
    file_row = File(
        content_hash="abc123",
        filename="retry.txt",
        mime_type="text/plain",
        size_bytes=4,
        local_path=str(tmp_path / "retry.txt"),
    )
    db_session.add(file_row)
    await db_session.flush()

    first = await _attach_files(db_session, message, [file_row.id])
    second = await _attach_files(db_session, message, [file_row.id])

    assert len(first) == 1
    assert len(second) == 1
    assert file_row.message_id == message.id
