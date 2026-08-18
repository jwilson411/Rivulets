"""filesystem builtin tool's sandbox/path-escape guard (tools/builtin/filesystem.py).

No prior test file exercised this tool at all. get_settings().workspace_dir
points at the real per-session tempdir set up in conftest.py (RIVULETS_
WORKSPACE_DIR), so these tests read/write real files under
<tempdir>/tool_fs rather than mocking the filesystem.
"""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from rivulets.tools.builtin.filesystem import list_files, read_file, write_file

assert list_files.entrypoint is not None
assert read_file.entrypoint is not None
assert write_file.entrypoint is not None
_list_files = cast("Callable[..., list[str]]", list_files.entrypoint)
_read_file = cast("Callable[..., str]", read_file.entrypoint)
_write_file = cast("Callable[..., str]", write_file.entrypoint)


def test_write_then_read_round_trip() -> None:
    result = _write_file(path="notes/todo.txt", content="buy milk")
    assert "Wrote 8 bytes to notes/todo.txt" == result
    assert _read_file(path="notes/todo.txt") == "buy milk"


def test_write_file_creates_parent_directories() -> None:
    _write_file(path="a/b/c/deep.txt", content="hi")
    assert _read_file(path="a/b/c/deep.txt") == "hi"


def test_list_files_returns_sorted_names() -> None:
    _write_file(path="listing/b.txt", content="")
    _write_file(path="listing/a.txt", content="")
    _write_file(path="listing/c.txt", content="")
    assert _list_files(directory="listing") == ["a.txt", "b.txt", "c.txt"]


def test_list_files_default_directory_is_sandbox_root() -> None:
    _write_file(path="at_root.txt", content="")
    assert "at_root.txt" in _list_files()


def test_read_file_missing_path_raises() -> None:
    with pytest.raises(ValueError, match="is not a file"):
        _read_file(path="does/not/exist.txt")


def test_read_file_on_directory_raises() -> None:
    _write_file(path="adir/inner.txt", content="x")
    with pytest.raises(ValueError, match="is not a file"):
        _read_file(path="adir")


def test_list_files_on_missing_directory_raises() -> None:
    with pytest.raises(ValueError, match="is not a directory"):
        _list_files(directory="nope")


def test_list_files_on_a_file_raises() -> None:
    _write_file(path="just_a_file.txt", content="x")
    with pytest.raises(ValueError, match="is not a directory"):
        _list_files(directory="just_a_file.txt")


def test_read_file_relative_escape_is_blocked() -> None:
    with pytest.raises(ValueError, match="escapes the workspace sandbox"):
        _read_file(path="../../etc/passwd")


def test_write_file_relative_escape_is_blocked() -> None:
    with pytest.raises(ValueError, match="escapes the workspace sandbox"):
        _write_file(path="../escape.txt", content="pwned")


def test_list_files_relative_escape_is_blocked() -> None:
    with pytest.raises(ValueError, match="escapes the workspace sandbox"):
        _list_files(directory="..")


def test_configured_working_directory_is_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        "rivulets.tools.builtin.filesystem.filesystem_root", lambda: project.resolve()
    )
    result = _write_file(path="hello.txt", content="ship it")
    assert result == "Wrote 7 bytes to hello.txt"
    assert (project / "hello.txt").read_text() == "ship it"
    assert _list_files() == ["hello.txt"]


def test_absolute_path_does_not_bypass_the_sandbox() -> None:
    """pathlib's `/` operator discards the left operand entirely when the
    right one is absolute (Path("/root") / "/etc/passwd" == Path("/etc/passwd")),
    so `_resolve`'s `root / relative_path` join doesn't actually confine an
    absolute input to the sandbox on its own -- it's the parents-of-root
    check afterward that catches it. Exercised explicitly since a future
    change to that check (e.g. reordering it before `.resolve()`) could
    silently reopen this path."""
    with pytest.raises(ValueError, match="escapes the workspace sandbox"):
        _read_file(path="/etc/passwd")
