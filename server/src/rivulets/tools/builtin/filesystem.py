"""File System built-in tool (FR-8.1).

Confined to a dedicated sandbox directory under the workspace root — not
the SQLite DB, logs, or rivulet file store — so a tool call can never read
or clobber Rivulets' own state. Every path is resolved and checked to
stay inside that root before touching disk (a naive f-string join would
let `..` escape it).
"""

from pathlib import Path

from agno.tools import tool

from rivulets.config import get_settings


def _sandbox_root() -> Path:
    root = get_settings().workspace_dir / "tool_fs"
    root.mkdir(parents=True, exist_ok=True)
    # Resolved once here so _resolve()'s containment check compares two
    # canonical paths -- otherwise a workspace_dir sitting behind a
    # symlink (e.g. macOS's /tmp -> /private/tmp) makes every legitimate
    # nested path look like it "escapes" the sandbox, since resolving
    # `root / relative_path` follows the symlink but this unresolved
    # root never did.
    return root.resolve()


def _resolve(relative_path: str) -> Path:
    root = _sandbox_root()
    resolved = (root / relative_path).resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"Path '{relative_path}' escapes the workspace sandbox")
    return resolved


@tool
def list_files(directory: str = ".") -> list[str]:
    """List files and directories within the workspace-bounded sandbox."""
    target = _resolve(directory)
    if not target.is_dir():
        raise ValueError(f"'{directory}' is not a directory")
    return sorted(p.name for p in target.iterdir())


@tool
def read_file(path: str) -> str:
    """Read a text file within the workspace-bounded sandbox."""
    target = _resolve(path)
    if not target.is_file():
        raise ValueError(f"'{path}' is not a file")
    return target.read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """Write a text file within the workspace-bounded sandbox, creating
    parent directories as needed."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"
