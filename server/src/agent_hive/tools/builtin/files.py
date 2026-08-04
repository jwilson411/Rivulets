"""File attachment access built-in tool (FR-10.3).

Lets an invoked agent read the content of a file shared in its thread, via
the same content-addressed local store api/files.py writes uploads to.
The file reference in a thread message always resolves to a local path
whether the file was uploaded on this node or replicated from a peer's
(sync/apply.py's apply_remote_file_change) — this tool doesn't need to
know or care which.
"""

import sqlite3
from pathlib import Path

from agno.tools import tool

from agent_hive.config import get_settings

_MAX_TEXT_BYTES = 200_000  # keep tool output within a reasonable context budget
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = ("application/json",)


@tool
def read_attached_file(file_id: str) -> str:
    """Read the text content of a file attached to a thread message, given
    its file_id (as returned by the file upload API). Binary files and
    files whose content hasn't synced to this node yet return a
    description instead of raw bytes."""
    db_path = get_settings().db_path
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT filename, mime_type, size_bytes, local_path FROM file WHERE id = ?",
            (file_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"No file found with id {file_id!r}")
    filename, mime_type, size_bytes, local_path = row

    path = Path(local_path)
    if not path.exists():
        return (
            f"File {filename!r} ({mime_type}, {size_bytes} bytes) is registered but its "
            "content hasn't synced to this node yet."
        )

    if not mime_type.startswith(_TEXT_MIME_PREFIXES) and mime_type not in _TEXT_MIME_TYPES:
        return f"File {filename!r} is {mime_type} ({size_bytes} bytes) — not a text file."

    data = path.read_bytes()
    text = data[:_MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    if len(data) > _MAX_TEXT_BYTES:
        text += f"\n\n[truncated, showing first {_MAX_TEXT_BYTES} of {len(data)} bytes]"
    return text
