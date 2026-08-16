"""File attachment access built-in tool (FR-10.3, #105).

Lets an invoked agent read the content of a file shared in its rivulet, via
the same content-addressed local store api/files.py writes uploads to.
The file reference in a rivulet message always resolves to a local path
whether the file was uploaded on this node or replicated from a peer's
(sync/apply.py's apply_remote_file_change) — this tool doesn't need to
know or care which. Bytes a lazy sync hasn't fetched yet are pulled on
demand from known sources/connected peers (#391), the same recovery
api/files.py's download_file uses.
"""

import sqlite3

from agno.media import Image
from agno.tools import tool
from agno.tools.function import ToolResult

from rivulets.config import get_settings
from rivulets.validation import local_path_for_content_hash

_MAX_TEXT_BYTES = 200_000  # keep tool output within a reasonable context budget
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = ("application/json",)
_IMAGE_MIME_PREFIX = "image/"


@tool
async def read_attached_file(file_id: str) -> str | ToolResult:
    """Read the content of a file attached to a rivulet message, given its
    file_id (as returned by the file upload API). Text/JSON files are
    returned as text. Image files are returned as actual visible image
    content (#105) so a vision-capable model can see them, not just a
    description. Other binary files, and files whose content isn't held by
    this node or any reachable peer, return a description instead of raw
    bytes."""
    db_path = get_settings().db_path
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT filename, mime_type, size_bytes, content_hash, synced_to_nodes "
            "FROM file WHERE id = ?",
            (file_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"No file found with id {file_id!r}")
    filename, mime_type, size_bytes, content_hash, synced_to_nodes = row

    # Re-derived from files_dir + content_hash rather than trusting the
    # stored local_path column (#239) -- a hash written by older or buggy
    # code could otherwise point outside files_dir.
    try:
        path = local_path_for_content_hash(content_hash)
    except ValueError as exc:
        raise ValueError(f"File {filename!r} has an invalid content hash") from exc
    if not path.exists():
        # Lazy sync (sync.eager_files_lan/_wan, #123) may have deferred
        # fetching this file's bytes -- try on demand from known sources
        # and connected peers (#391), the same recovery api/files.py's
        # download_file uses, before giving up. Imported here rather than
        # at module top: sync/apply.py imports agentos/service.py, which
        # imports tools/builtin/, so a module-level import back into
        # sync/apply.py would be a cycle.
        from rivulets.sync.apply import fetch_file_content_from_known_sources

        await fetch_file_content_from_known_sources(content_hash, synced_to_nodes)
    if not path.exists():
        return (
            f"File {filename!r} ({mime_type}, {size_bytes} bytes) is registered but its "
            "content hasn't synced to this node yet."
        )

    if mime_type.startswith(_IMAGE_MIME_PREFIX):
        # ToolResult.images is merged into the model's next-turn context by
        # agno (agno/models/base.py's function-call-result handling) — this
        # is the same mechanism agno's own built-in tools (e.g.
        # tools/file_generation.py, tools/models_labs.py) use to hand media
        # back to the model, not a new subsystem. If the resolved model
        # doesn't actually support vision, the provider API call itself
        # fails and surfaces as a normal run error (dispatch/service.py's
        # existing error handling) rather than being silently dropped.
        return ToolResult(
            content=f"Attached image {filename!r} ({mime_type}, {size_bytes} bytes) is "
            "shown below.",
            images=[Image(filepath=str(path), mime_type=mime_type)],
        )

    if not mime_type.startswith(_TEXT_MIME_PREFIXES) and mime_type not in _TEXT_MIME_TYPES:
        return f"File {filename!r} is {mime_type} ({size_bytes} bytes) — not a text file."

    data = path.read_bytes()
    text = data[:_MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    if len(data) > _MAX_TEXT_BYTES:
        text += f"\n\n[truncated, showing first {_MAX_TEXT_BYTES} of {len(data)} bytes]"
    return text
