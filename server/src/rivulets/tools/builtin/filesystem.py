"""File System built-in tool (FR-8.1).

Confined to the owner-chosen working directory (Settings → Files), or to
a dedicated sandbox under the workspace root when none is set — never
the SQLite DB, logs, or rivulet file store, so a tool call can never
read or clobber Rivulets' own state. Every path is resolved and checked
to stay inside that root before touching disk (a naive f-string join
would let `..` escape it).
"""

from pathlib import Path

from agno.tools import tool

from rivulets.working_directory import (
    bind_working_directory,
    filesystem_root,
    normalize_working_directory,
    resolve_effective_root,
)


def _sandbox_root() -> Path:
    # Resolved once here so _resolve()'s containment check compares two
    # canonical paths -- otherwise a workspace_dir sitting behind a
    # symlink (e.g. macOS's /tmp -> /private/tmp) makes every legitimate
    # nested path look like it "escapes" the sandbox, since resolving
    # `root / relative_path` follows the symlink but this unresolved
    # root never did.
    return filesystem_root()


def _resolve(relative_path: str) -> Path:
    root = _sandbox_root()
    resolved = (root / relative_path).resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"Path '{relative_path}' escapes the workspace sandbox")
    return resolved


@tool
def list_files(directory: str = ".") -> list[str]:
    """List files and directories in the configured working directory."""
    target = _resolve(directory)
    if not target.is_dir():
        raise ValueError(f"'{directory}' is not a directory")
    return sorted(p.name for p in target.iterdir())


@tool
def read_file(path: str) -> str:
    """Read a text file in the configured working directory."""
    target = _resolve(path)
    if not target.is_file():
        raise ValueError(f"'{path}' is not a file")
    return target.read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """Write a text file in the configured working directory, creating
    parent directories as needed."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


@tool
def set_working_directory(path: str = "") -> str:
    """Set the project folder for this conversation only. Does not change
    the channel (river) default — later conversations in the same channel
    keep using that default unless they override too. Pass an empty path
    to clear this conversation's override and inherit the channel folder
    (or Settings, or the built-in sandbox). The folder must already exist
    on this machine."""
    try:
        normalized = normalize_working_directory(path)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if normalized is None:
        bind_working_directory(resolve_effective_root(None))
        return (
            "This conversation now inherits the channel project folder "
            "(or the Settings default if the channel has none)."
        )
    bind_working_directory(Path(normalized))
    return f"This conversation's project folder is now {normalized}."
