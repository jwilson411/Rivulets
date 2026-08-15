"""api/files.py's download/info routes -- test_message_attachments.py only
ever exercises upload + attach-to-message, never GET /files/{id} or
GET /files/{id}/info directly."""

import json

import pytest
from fastapi.testclient import TestClient

from rivulets.config import get_settings
from rivulets.db.models import File as FileRow
from rivulets.db.session import session_scope
from rivulets.sync.engine import PeerInfo


def _upload(
    client: TestClient, headers: dict[str, str], name: str, content: bytes
) -> dict[str, str]:
    response = client.post(
        "/api/v1/files/upload",
        files={"upload": (name, content, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_download_file_returns_the_uploaded_bytes(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    uploaded = _upload(client, auth_headers, "notes.txt", b"hello download")

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == b"hello download"
    assert response.headers["content-type"].startswith("text/plain")


def test_download_file_returns_404_for_unknown_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/files/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


async def test_download_file_lazily_fetches_from_a_known_source_when_missing_locally(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #123: a file whose bytes aren't on disk yet (lazy sync
    deferred the fetch, or it was otherwise lost) is still fetchable on
    demand from a peer recorded in File.synced_to_nodes, rather than
    404ing outright."""
    uploaded = _upload(client, auth_headers, "notes.txt", b"lazy content")
    content_hash = uploaded["content_hash"]
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert local_path.exists()
    local_path.unlink()  # simulate the bytes never having landed locally

    async with session_scope() as db:
        row = await db.get(FileRow, uploaded["file_id"])
        assert row is not None
        row.synced_to_nodes = json.dumps(["node-b"])
        await db.commit()

    class _FakeEngine:
        running = True

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            assert peer_id == "node-b"
            assert requested_hash == content_hash
            return b"lazy content"

    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == b"lazy content"


async def test_download_file_falls_back_to_connected_peers_when_the_origin_is_gone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#363: synced_to_nodes only ever records the gossip origin, but a
    peer that eager-fetched the bytes from that origin can still serve
    them after the origin goes offline. The on-demand fetch must try
    currently-connected peers too, not 404 because the one recorded
    source is unreachable."""
    uploaded = _upload(client, auth_headers, "notes.txt", b"held by another peer")
    content_hash = uploaded["content_hash"]
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    local_path.unlink()

    async with session_scope() as db:
        row = await db.get(FileRow, uploaded["file_id"])
        assert row is not None
        row.synced_to_nodes = json.dumps(["node-a"])  # the offline origin
        await db.commit()

    asked: list[str] = []

    class _FakeEngine:
        running = True

        async def list_peers(self) -> list[PeerInfo]:
            return [
                PeerInfo(peer_id="node-b", address="/ip4/10.0.0.7/tcp/4001", connected=True),
                PeerInfo(peer_id="node-c", address="/ip4/10.0.0.9/tcp/4001", connected=True),
            ]

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            asked.append(peer_id)
            if peer_id == "node-a":  # origin offline -- transport failure
                return None
            if peer_id == "node-b":  # connected but never fetched the bytes
                return None
            assert requested_hash == content_hash
            return b"held by another peer"

    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == b"held by another peer"
    # Recorded sources are asked first, then the remaining connected peers.
    assert asked == ["node-a", "node-b", "node-c"]


async def test_download_file_returns_404_when_content_missing_and_no_known_source(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    uploaded = _upload(client, auth_headers, "notes.txt", b"gone")
    content_hash = uploaded["content_hash"]
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    local_path.unlink()

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 404


async def test_download_file_drops_mismatched_content_from_a_known_source(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#288: a peer recorded in synced_to_nodes can claim to have a given
    content_hash but hand back different bytes -- those must never be
    written to disk under that hash's name, and the download must still
    404 rather than silently serving the wrong content."""
    uploaded = _upload(client, auth_headers, "notes.txt", b"lazy content")
    content_hash = uploaded["content_hash"]
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    local_path.unlink()

    async with session_scope() as db:
        row = await db.get(FileRow, uploaded["file_id"])
        assert row is not None
        row.synced_to_nodes = json.dumps(["node-b"])
        await db.commit()

    class _FakeEngine:
        running = True

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            return b"these bytes do not hash to content_hash"

    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 404
    assert not local_path.exists()


async def test_download_file_returns_404_for_invalid_stored_content_hash(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#288: a row whose stored content_hash isn't a valid digest (e.g.
    written by older/buggy code) must 404, not raise an unhandled
    ValueError that surfaces as a 500."""
    uploaded = _upload(client, auth_headers, "notes.txt", b"whatever")

    async with session_scope() as db:
        row = await db.get(FileRow, uploaded["file_id"])
        assert row is not None
        row.content_hash = "not-a-valid-hex-digest"
        await db.commit()

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 404


def test_file_info_returns_metadata_without_the_body(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    uploaded = _upload(client, auth_headers, "info.txt", b"metadata only")

    response = client.get(f"/api/v1/files/{uploaded['file_id']}/info", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "info.txt"
    assert body["size_bytes"] == len(b"metadata only")
    assert body["content_hash"] == uploaded["content_hash"]


def test_file_info_returns_404_for_unknown_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/files/does-not-exist/info", headers=auth_headers)
    assert response.status_code == 404
