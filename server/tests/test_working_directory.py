"""working_directory helpers — path validation, listing, and the default
sandbox fallback the filesystem / code-exec tools share."""

from pathlib import Path

import pytest

from rivulets.config import get_settings
from rivulets.working_directory import (
    create_directory,
    default_filesystem_root,
    filesystem_root,
    list_directories,
    normalize_working_directory,
    stored_working_directory,
)


def test_normalize_none_and_blank_mean_default_sandbox() -> None:
    assert normalize_working_directory(None) is None
    assert normalize_working_directory("   ") is None


def test_normalize_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="folder path"):
        normalize_working_directory(12)


def test_normalize_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a folder"):
        normalize_working_directory(str(tmp_path / "missing"))


def test_normalize_rejects_a_file(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("x")
    with pytest.raises(ValueError, match="not a folder"):
        normalize_working_directory(str(file_path))


def test_normalize_rejects_workspace_dir_and_children() -> None:
    workspace = get_settings().workspace_dir
    with pytest.raises(ValueError, match="own data directory"):
        normalize_working_directory(str(workspace))
    nested = workspace / "files"
    nested.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="own data directory"):
        normalize_working_directory(str(nested))


def test_normalize_accepts_an_existing_folder(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    assert normalize_working_directory(str(project)) == str(project.resolve())


def test_filesystem_root_falls_back_to_tool_fs_when_unset() -> None:
    assert stored_working_directory() is None
    assert filesystem_root() == default_filesystem_root()
    assert filesystem_root().name == "tool_fs"


def test_list_directories_skips_files_and_dotfolders(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "readme.md").write_text("hi")
    listing = list_directories(str(tmp_path))
    assert [entry.name for entry in listing.entries] == ["keep"]
    assert listing.parent == str(tmp_path.resolve().parent)


def test_create_directory_makes_the_folder(tmp_path: Path) -> None:
    created = create_directory(str(tmp_path), "workspace")
    assert created == (tmp_path / "workspace").resolve()
    assert created.is_dir()


def test_create_directory_rejects_existing(tmp_path: Path) -> None:
    (tmp_path / "dup").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        create_directory(str(tmp_path), "dup")
