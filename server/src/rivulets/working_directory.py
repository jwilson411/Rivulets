"""Node-local folder agents use to read, write, and run code.

Rivulets' own data lives under `workspace_dir` (`~/.rivulets` by default).
The filesystem and code-execution tools used to be confined to hidden
sandbox subfolders there, which meant agents could not work on a real
project. The owner picks a folder on this machine; that path is stored
and is never synced — an absolute path is meaningless on another peer.

Resolution, most specific first:

  1. The rivulet's own override (`rivulet.working_directory`)
  2. The channel (river) default (`channel.working_directory`)
  3. The workspace Settings default (`tools.working_directory`)
  4. The original sandbox directories so existing installs keep working

A rivulet can change (1) without touching the river default. Channel
tools must not write the river folder — only the human Settings/channel
UI (or a rivulet-scoped tool) may.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from rivulets.config import get_settings

SETTING_KEY = "tools.working_directory"

# Bound for the duration of one agent run so filesystem / code-exec tools
# see the rivulet/channel/workspace resolution for *this* conversation,
# not the process-wide Settings default. Unset outside a run.
_active_root: ContextVar[Path | None] = ContextVar("working_directory_root", default=None)

# Single path-segment names only — a slash would let create_directory
# write outside the listed parent.
_INVALID_FOLDER_NAMES = frozenset({".", ".."})


def default_filesystem_root() -> Path:
    root = get_settings().workspace_dir / "tool_fs"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def default_code_exec_root() -> Path:
    root = get_settings().workspace_dir / "tool_code_exec"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def normalize_working_directory(value: object) -> str | None:
    """Validate a PATCH/tool value. Empty/None means 'use the built-in
    sandbox'. Otherwise the path must already be a directory on this
    machine, and must not be Rivulets' own data tree."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Working directory must be a folder path.")
    stripped = value.strip()
    if not stripped:
        return None
    path = Path(stripped).expanduser()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"Couldn't resolve '{stripped}'.") from exc
    if not resolved.is_dir():
        raise ValueError(f"'{stripped}' is not a folder on this machine.")
    workspace = get_settings().workspace_dir.resolve()
    if resolved == workspace or workspace in resolved.parents:
        raise ValueError("Pick a project folder, not Rivulets' own data directory.")
    return str(resolved)


def live_directory(value: str | None) -> Path | None:
    """A stored path that is still a directory on this machine."""
    if not value or not value.strip():
        return None
    path = Path(value)
    if not path.is_dir():
        return None
    return path.resolve()


def stored_working_directory() -> Path | None:
    """The workspace Settings folder, if it is still a directory on disk."""
    return live_directory(_read_stored_value())


def resolve_effective_path(*candidates: str | None) -> str | None:
    """First still-on-disk candidate, then the workspace Settings default.

    Returns None when every layer is unset or missing — callers that need
    a sandbox fall through to `filesystem_root` / `code_exec_root`.
    """
    for raw in candidates:
        live = live_directory(raw)
        if live is not None:
            return str(live)
    stored = stored_working_directory()
    return str(stored) if stored is not None else None


def resolve_effective_root(*candidates: str | None) -> Path:
    """Same resolution as `resolve_effective_path`, with the filesystem
    sandbox as the last resort so tools always have a directory."""
    resolved = resolve_effective_path(*candidates)
    return Path(resolved) if resolved is not None else default_filesystem_root()


@contextmanager
def using_working_directory(root: Path) -> Generator[None]:
    """Bind `filesystem_root` / `code_exec_root` for this task."""
    token = _active_root.set(root.resolve())
    try:
        yield
    finally:
        _active_root.reset(token)


def bind_working_directory(root: Path) -> None:
    """Replace the bound root mid-run (rivulet override, same conversation)."""
    _active_root.set(root.resolve())


def filesystem_root() -> Path:
    bound = _active_root.get()
    if bound is not None:
        return bound
    return stored_working_directory() or default_filesystem_root()


def code_exec_root() -> Path:
    bound = _active_root.get()
    if bound is not None:
        return bound
    return stored_working_directory() or default_code_exec_root()


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    path: str


@dataclass(frozen=True)
class DirectoryListing:
    path: str
    parent: str | None
    entries: list[DirectoryEntry]


def list_directories(path: str | None) -> DirectoryListing:
    """Immediate child folders of `path` (home if omitted). Hidden names
    (leading '.') are omitted so the picker stays a project-folder
    chooser, not a full file manager."""
    target = _resolve_existing_directory(path) if path else Path.home().resolve()
    parent = target.parent
    parent_path = str(parent) if parent != target else None
    entries: list[DirectoryEntry] = []
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise ValueError(f"Couldn't read '{target}'.") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        entries.append(DirectoryEntry(name=child.name, path=str(child.resolve())))
    entries.sort(key=lambda entry: entry.name.lower())
    return DirectoryListing(path=str(target), parent=parent_path, entries=entries)


def create_directory(parent: str, name: str) -> Path:
    folder_name = name.strip()
    if not folder_name or folder_name in _INVALID_FOLDER_NAMES:
        raise ValueError("Enter a folder name.")
    if "/" in folder_name or "\\" in folder_name:
        raise ValueError("Folder name can't contain slashes.")
    parent_path = _resolve_existing_directory(parent)
    dest = (parent_path / folder_name).resolve()
    if dest.parent != parent_path:
        raise ValueError("Folder name can't leave this location.")
    try:
        dest.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"'{folder_name}' already exists here.") from exc
    except OSError as exc:
        raise ValueError(f"Couldn't create '{folder_name}'.") from exc
    return dest


def _resolve_existing_directory(path: str) -> Path:
    stripped = path.strip()
    if not stripped:
        raise ValueError("Choose a folder.")
    try:
        resolved = Path(stripped).expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"Couldn't resolve '{stripped}'.") from exc
    if not resolved.is_dir():
        raise ValueError(f"'{stripped}' is not a folder on this machine.")
    return resolved


def _read_stored_value() -> str | None:
    db_path = get_settings().db_path
    if not db_path.exists():
        return None
    try:
        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_settings WHERE key = ?",
                (SETTING_KEY,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value
