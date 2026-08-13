"""Shared validators for filesystem path segments derived from untrusted
input.

`files_dir`/`tools_dir` are both flat, content-addressed-ish stores where a
single field from a request or a synced peer's payload becomes a literal
path segment (`files_dir/hash[:2]/hash`, `tools_dir/{name}.py`). The HTTP
create paths (api/files.py, api/tools.py) validate that field before it
ever reaches a path join. Issue #239 found that sync/apply.py's peer-payload
path did the same joins without the same checks — a malicious mesh peer
(sync gives every peer the workspace PSK, but not filesystem access outside
it) could send `content_hash="../../etc/passwd"` and have it read back, or
an absolute value that pathlib's `/` operator would swap in wholesale
(`Path("/data/files") / "/etc/passwd"` -> `/etc/passwd`). Centralizing here
means both paths reuse the exact same validation instead of drifting apart
again."""

import re
from pathlib import Path

from rivulets.config import get_settings

# A bare Python identifier: letters, digits, underscores, not digit-leading.
# Used as `f"{name}.py"` under tools_dir -- this charset can never contain
# "/" or "..", so it can't escape tools_dir.
TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A SHA-256 hex digest: exactly 64 lowercase hex characters. Used as a path
# segment (and its own first-2-chars shard directory) under files_dir --
# this charset can never contain "/" or "..", so it can't escape files_dir
# or be reinterpreted as an absolute path by pathlib's `/` operator.
CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def local_path_for_content_hash(content_hash: str) -> Path:
    """The one place that turns a content_hash into an on-disk path.

    Readers should call this rather than trusting a stored `File.local_path`
    string (#239's third bullet) — a hash written by older or buggy code
    could point outside files_dir, and re-deriving from files_dir + hash
    every time means a bad stored value can't be replayed on read.

    Raises ValueError if content_hash isn't a valid hex digest, or (belt and
    suspenders alongside the regex, which already rules this out) if the
    resolved path would land outside files_dir.
    """
    if not CONTENT_HASH_RE.match(content_hash):
        raise ValueError(f"Invalid content_hash: {content_hash!r}")
    files_dir = get_settings().files_dir
    local_path = files_dir / content_hash[:2] / content_hash
    if not local_path.resolve().is_relative_to(files_dir.resolve()):
        raise ValueError(f"content_hash escapes files_dir: {content_hash!r}")
    return local_path
