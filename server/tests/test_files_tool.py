"""read_attached_file builtin tool (tools/builtin/files.py).

No prior test file exercised this tool at all. Like db_query.py, this
tool opens get_settings().db_path directly with sqlite3 rather than
through the app's async SQLAlchemy engine, so tests seed a real `file`
table row in that real (per-session tempdir) file rather than using the
`client`/`db_session` fixtures.
"""

import sqlite3
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from rivulets.config import get_settings
from rivulets.tools.builtin.files import read_attached_file

assert read_attached_file.entrypoint is not None
_call = cast("Callable[..., str]", read_attached_file.entrypoint)


@pytest.fixture
def file_db() -> Iterator[None]:
    db_path = get_settings().db_path
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE file (id TEXT PRIMARY KEY, filename TEXT, "
            "mime_type TEXT, size_bytes INTEGER, local_path TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    yield
    db_path.unlink(missing_ok=True)


def _insert_file_row(
    file_id: str, filename: str, mime_type: str, size_bytes: int, local_path: str
) -> None:
    conn = sqlite3.connect(get_settings().db_path)
    try:
        conn.execute(
            "INSERT INTO file (id, filename, mime_type, size_bytes, local_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, filename, mime_type, size_bytes, local_path),
        )
        conn.commit()
    finally:
        conn.close()


def test_unknown_file_id_raises(file_db: None) -> None:
    with pytest.raises(ValueError, match="No file found with id"):
        _call(file_id="does-not-exist")


def test_reads_text_file_content(file_db: None, tmp_path: Path) -> None:
    content_path = tmp_path / "notes.txt"
    content_path.write_text("hello world")
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "notes.txt", "text/plain", 11, str(content_path))

    assert _call(file_id=file_id) == "hello world"


def test_reads_json_file_content(file_db: None, tmp_path: Path) -> None:
    content_path = tmp_path / "data.json"
    content_path.write_text('{"a": 1}')
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "data.json", "application/json", 8, str(content_path))

    assert _call(file_id=file_id) == '{"a": 1}'


def test_binary_file_returns_description_not_content(file_db: None, tmp_path: Path) -> None:
    content_path = tmp_path / "photo.png"
    content_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "photo.png", "image/png", 8, str(content_path))

    result = _call(file_id=file_id)
    assert "photo.png" in result
    assert "image/png" in result
    assert "not a text file" in result


def test_unsynced_file_returns_description(file_db: None, tmp_path: Path) -> None:
    """local_path is registered in the DB but the content hasn't arrived
    on this node yet (sync/apply.py's replication is async/eventual) --
    the file doesn't exist on disk yet."""
    missing_path = tmp_path / "not_here_yet.txt"
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "not_here_yet.txt", "text/plain", 42, str(missing_path))

    result = _call(file_id=file_id)
    assert "not_here_yet.txt" in result
    assert "hasn't synced to this node yet" in result


def test_large_text_file_is_truncated(file_db: None, tmp_path: Path) -> None:
    big_content = "x" * 250_000
    content_path = tmp_path / "big.txt"
    content_path.write_text(big_content)
    file_id = str(uuid.uuid4())
    _insert_file_row(file_id, "big.txt", "text/plain", len(big_content), str(content_path))

    result = _call(file_id=file_id)
    assert result.startswith("x" * 100)
    assert "[truncated, showing first 200000 of 250000 bytes]" in result
    # The truncation notice is appended, not counted as part of the
    # capped content itself.
    assert len(result) < len(big_content) + 100
