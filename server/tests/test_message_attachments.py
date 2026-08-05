"""Message attachment visibility (FR-10.1's "into threads" -- the read
side). Before this, File.message_id got set on attach but MessageOut had
no way to expose it, so a UI could upload+attach a file but never learn
which messages had attachments, including its own just-created message.
"""

from fastapi.testclient import TestClient


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
