"""Workspace dir/file permission hardening (#244): docs/security.md claims
everything under ~/.rivulets/ is owner-only, but nothing actually enforced
that until main.py's process umask and Settings.ensure_workspace_dirs's
explicit chmod calls. POSIX-only -- os.chmod's group/world bits are not
meaningful on Windows.
"""

import stat
import sys
from pathlib import Path

import pytest

from rivulets.config import Settings

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file-mode bits aren't meaningful on Windows"
)


def _world_or_group_readable(path: Path) -> bool:
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def test_ensure_workspace_dirs_creates_owner_only_dirs(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    settings = Settings(workspace_dir=workspace_dir)

    settings.ensure_workspace_dirs()

    for d in (
        settings.workspace_dir,
        settings.files_dir,
        settings.tools_dir,
        settings.logs_dir,
        settings.backups_dir,
        settings.sync_dir,
    ):
        assert not _world_or_group_readable(d), f"{d} is group/world accessible"
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_ensure_workspace_dirs_tightens_preexisting_permissive_dir(tmp_path: Path) -> None:
    """Upgrade path: an install predating #244 may already have a
    world-readable workspace dir on disk -- the next startup must fix it in
    place, not just apply the restrictive mode to newly created installs."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(mode=0o755)
    settings = Settings(workspace_dir=workspace_dir)

    settings.ensure_workspace_dirs()

    assert stat.S_IMODE(workspace_dir.stat().st_mode) == 0o700


def test_ensure_workspace_dirs_tightens_preexisting_permissive_db_files(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(mode=0o700)
    settings = Settings(workspace_dir=workspace_dir)
    settings.db_path.write_text("")
    settings.db_path.chmod(0o644)
    settings.credential_fallback_db_path.write_text("")
    settings.credential_fallback_db_path.chmod(0o644)

    settings.ensure_workspace_dirs()

    assert stat.S_IMODE(settings.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.credential_fallback_db_path.stat().st_mode) == 0o600
