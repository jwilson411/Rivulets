"""read_attached_file builtin tool (tools/builtin/files.py).

No prior test file exercised this tool at all. Like db_query.py, this
tool opens get_settings().db_path directly with sqlite3 rather than
through the app's async SQLAlchemy engine, so tests seed a real `file`
table row in that real (per-session tempdir) file rather than using the
`client`/`db_session` fixtures.

Content is written under files_dir/hash[:2]/hash (content-addressed, same
layout api/files.py's upload path uses) and rows carry `content_hash`, not
`local_path` -- #239: this tool re-derives the on-disk path from
files_dir + content_hash rather than trusting a stored local_path column,
so a row can no longer point content resolution at an arbitrary path.

The sync engine singleton is initialized (but never started) by the
file_db fixture, same as conftest's db_session: #391's on-demand fetch
calls get_sync_engine(), which raises when the singleton was never
init'd -- something create_app() does in the real process but nothing
here would otherwise do.
"""

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import cast

import pytest
from agno.tools.function import ToolResult

from rivulets.config import get_settings
from rivulets.sync.engine import (
    PeerInfo,
    init_sync_engine,
    reset_sync_engine_for_testing,
)
from rivulets.tools.builtin.files import read_attached_file

assert read_attached_file.entrypoint is not None
_call = cast("Callable[..., Awaitable[str | ToolResult]]", read_attached_file.entrypoint)


@pytest.fixture
def file_db() -> Iterator[None]:
    db_path = get_settings().db_path
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE file (id TEXT PRIMARY KEY, filename TEXT, "
            "mime_type TEXT, size_bytes INTEGER, content_hash TEXT, "
            "synced_to_nodes TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    reset_sync_engine_for_testing()
    init_sync_engine(get_settings().sync_dir)
    yield
    reset_sync_engine_for_testing()
    db_path.unlink(missing_ok=True)


def _insert_file_row(
    file_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    content_hash: str,
    synced_to_nodes: str | None = None,
) -> None:
    conn = sqlite3.connect(get_settings().db_path)
    try:
        conn.execute(
            "INSERT INTO file (id, filename, mime_type, size_bytes, content_hash, "
            "synced_to_nodes) VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, filename, mime_type, size_bytes, content_hash, synced_to_nodes),
        )
        conn.commit()
    finally:
        conn.close()


def _write_content(content_hash: str, data: bytes) -> None:
    path = get_settings().files_dir / content_hash[:2] / content_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def test_unknown_file_id_raises(file_db: None) -> None:
    with pytest.raises(ValueError, match="No file found with id"):
        await _call(file_id="does-not-exist")


async def test_reads_text_file_content(file_db: None) -> None:
    content_hash = "a" * 64
    _write_content(content_hash, b"hello world")
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "notes.txt", "text/plain", 11, content_hash)

    assert await _call(file_id=file_id) == "hello world"


async def test_reads_json_file_content(file_db: None) -> None:
    content_hash = "b" * 64
    _write_content(content_hash, b'{"a": 1}')
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "data.json", "application/json", 8, content_hash)

    assert await _call(file_id=file_id) == '{"a": 1}'


async def test_binary_file_returns_description_not_content(file_db: None) -> None:
    content_hash = "c" * 64
    _write_content(content_hash, b"PK\x03\x04")
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "archive.zip", "application/zip", 4, content_hash)

    result = await _call(file_id=file_id)
    assert isinstance(result, str)
    assert "archive.zip" in result
    assert "application/zip" in result
    assert "not a text file" in result


async def test_image_file_returns_tool_result_with_image_content(file_db: None) -> None:
    """#105: an image attachment is handed back as actual visible image
    content (agno's ToolResult.images), not the old "not a text file"
    description -- so a vision-capable model can see it."""
    content_hash = "d" * 64
    _write_content(content_hash, b"\x89PNG\r\n\x1a\n")
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "photo.png", "image/png", 8, content_hash)

    result = await _call(file_id=file_id)
    assert isinstance(result, ToolResult)
    assert "photo.png" in (result.content or "")
    assert result.images is not None
    assert len(result.images) == 1
    image = result.images[0]
    expected_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert str(image.filepath) == str(expected_path)
    assert image.mime_type == "image/png"


async def test_unsynced_file_returns_description(file_db: None) -> None:
    """A valid content_hash is registered in the DB but the content hasn't
    arrived on this node yet (sync/apply.py's replication is async/
    eventual) -- no bytes have been written under files_dir for it yet,
    and the sync engine isn't running, so the #391 on-demand fetch can't
    recover them either."""
    content_hash = "e" * 64
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "not_here_yet.txt", "text/plain", 42, content_hash)

    result = await _call(file_id=file_id)
    assert isinstance(result, str)
    assert "not_here_yet.txt" in result
    assert "hasn't synced to this node yet" in result


async def test_unsynced_file_is_fetched_on_demand_from_a_known_source(
    file_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#391: the tool previously reported "hasn't synced to this node yet"
    whenever the bytes weren't on disk, even when a reachable peer held
    them -- it must try the same on-demand fetch api/files.py's download
    route uses (#363) before giving up."""
    content = b"fetched for the tool"
    content_hash = hashlib.sha256(content).hexdigest()
    file_id = str(uuid.uuid4())
    _insert_file_row(
        file_id,
        "peer_held.txt",
        "text/plain",
        len(content),
        content_hash,
        synced_to_nodes=json.dumps(["node-b"]),
    )

    class _FakeEngine:
        running = True

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            assert peer_id == "node-b"
            assert requested_hash == content_hash
            return content

    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())

    assert await _call(file_id=file_id) == "fetched for the tool"
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert local_path.read_bytes() == content


async def test_large_text_file_is_truncated(file_db: None) -> None:
    big_content = "x" * 250_000
    content_hash = "f" * 64
    _write_content(content_hash, big_content.encode())
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "big.txt", "text/plain", len(big_content), content_hash)

    result = await _call(file_id=file_id)
    assert isinstance(result, str)
    assert result.startswith("x" * 100)
    assert "[truncated, showing first 200000 of 250000 bytes]" in result
    # The truncation notice is appended, not counted as part of the
    # capped content itself.
    assert len(result) < len(big_content) + 100


async def test_path_traversal_content_hash_is_rejected_not_read(file_db: None) -> None:
    """#239: a row whose content_hash isn't a valid hex digest (e.g. one
    written by pre-fix sync code, or a directly-tampered DB) must not be
    used to resolve a path outside files_dir -- it should surface as an
    error rather than silently reading whatever the string points at."""
    secret = get_settings().workspace_dir / "outside_files_dir.txt"
    secret.write_text("should never be read by this tool")
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "evil.txt", "text/plain", 4, "../outside_files_dir.txt")

    with pytest.raises(ValueError, match="invalid content hash"):
        await _call(file_id=file_id)
