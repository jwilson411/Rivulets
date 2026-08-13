"""main()'s NFR-3.4 bind-address guard and the happy-path dispatch into
uvicorn.run -- both isolated from the real network via monkeypatching.
`rivulets/__init__.py` does `from .main import main`, which rebinds the
`main` attribute on the `rivulets` package to the *function*, shadowing
the submodule -- both a plain `from rivulets import main` and
monkeypatch's own dotted-string patch targets (which also resolve via
getattr traversal starting at the top-level package) would silently grab
the function instead of the module. Pulling the real module straight out
of `sys.modules` (guaranteed present once `rivulets.main` has been
imported once, below) sidesteps that.
"""

import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rivulets.config import Settings
from rivulets.main import main as run_main

main_module: ModuleType = sys.modules["rivulets.main"]


@pytest.fixture(autouse=True)
def _restore_process_umask() -> Any:
    """#244: main() now calls os.umask(0o077), which is process-wide, not
    per-call state monkeypatch can undo -- without this, the first test
    below would permanently tighten every other test's file-creation mode
    for the rest of the suite."""
    original = os.umask(0o022)
    os.umask(original)
    yield
    os.umask(original)


def _settings(host: str, workspace_dir: Path) -> Settings:
    return Settings(app_server_host=host, app_server_port=9999, workspace_dir=workspace_dir)


def _noop_uvicorn_run(*_args: object, **_kwargs: object) -> None:
    return None


def test_main_refuses_to_start_on_disallowed_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main_module, "get_settings", lambda: _settings("0.0.0.1", tmp_path))

    with pytest.raises(SystemExit, match="app_server_host"):
        run_main()


def test_main_starts_uvicorn_on_allowed_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings("127.0.0.1", tmp_path)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    run_calls: list[dict[str, Any]] = []

    def fake_run(app: Any, **kwargs: Any) -> None:
        run_calls.append({"app": app, **kwargs})

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    run_main()

    assert tmp_path.exists() and (tmp_path / "files").exists()  # ensure_workspace_dirs ran for real
    assert len(run_calls) == 1
    assert run_calls[0]["host"] == "127.0.0.1"
    assert run_calls[0]["port"] == 9999


def test_main_allows_0_0_0_0_for_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings("0.0.0.0", tmp_path)  # noqa: S104
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module.uvicorn, "run", _noop_uvicorn_run)

    run_main()  # must not raise


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX umask bits aren't meaningful on Windows"
)
def test_main_sets_a_restrictive_process_umask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """docs/security.md's claim (#244) that everything under ~/.rivulets/
    is created with a restrictive umask -- verified here at the process
    level, since that's what actually governs any file/dir main() or code
    it calls creates without an explicit chmod."""
    settings = _settings("127.0.0.1", tmp_path)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module.uvicorn, "run", _noop_uvicorn_run)

    run_main()

    current_umask = os.umask(0o022)
    assert current_umask == 0o077
