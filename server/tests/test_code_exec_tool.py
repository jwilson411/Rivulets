"""execute_python builtin tool (tools/builtin/code_exec.py) — ADR-008 sandboxing.

is_available()/unavailable-path tests run on every platform/CI. The real
sandboxed-execution tests only run where this machine actually has the
backend ADR-008 calls for (sandbox-exec on macOS, firejail on Linux) --
GitHub-hosted `ubuntu-latest` runners have neither installed by default,
so those are skipped there rather than failing the build, the same way
this project already tolerates other environment-dependent gaps (see
tests/conftest.py's in-memory keyring backend for the analogous CI gap
on the credentials side).
"""

import socket
import sys
from collections.abc import Callable
from typing import cast

import pytest

from rivulets.config import get_settings
from rivulets.tools.builtin import code_exec
from rivulets.tools.builtin.code_exec import SandboxUnavailableError, execute_python, is_available

assert execute_python.entrypoint is not None
_call = cast("Callable[..., str]", execute_python.entrypoint)

_sandbox_ready = is_available()


def _which_returning(path: str | None) -> Callable[[str], str | None]:
    def fake_which(_name: str) -> str | None:
        return path

    return fake_which


def test_is_available_false_on_linux_without_firejail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning(None))
    assert is_available() is False


def test_is_available_true_on_linux_with_firejail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))
    assert is_available() is True


def test_is_available_false_on_macos_without_sandbox_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning(None))
    assert is_available() is False


def test_is_available_true_on_macos_with_sandbox_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/sandbox-exec"))
    assert is_available() is True


def test_is_available_false_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """No sandbox is wired up for Windows at all (see module docstring) --
    unavailable regardless of what's on PATH."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/anything"))
    assert is_available() is False


def test_execute_python_refuses_to_run_unsandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core guarantee: if no sandbox backend is available, code must
    never fall back to running directly in this process."""
    monkeypatch.setattr(code_exec, "is_available", lambda: False)
    with pytest.raises(SandboxUnavailableError, match="No sandbox backend available"):
        _call(code="print('should never run')")


@pytest.mark.skipif(not _sandbox_ready, reason="no sandbox backend installed on this machine")
def test_execute_python_runs_code_and_returns_stdout() -> None:
    result = _call(code="print('hello from sandbox')")
    assert "hello from sandbox" in result


@pytest.mark.skipif(not _sandbox_ready, reason="no sandbox backend installed on this machine")
def test_execute_python_reports_nonzero_exit_and_traceback() -> None:
    result = _call(code="raise ValueError('boom')")
    assert "Process exited with code" in result
    assert "ValueError: boom" in result


@pytest.mark.skipif(not _sandbox_ready, reason="no sandbox backend installed on this machine")
def test_execute_python_cannot_write_outside_the_sandbox_directory() -> None:
    target = get_settings().workspace_dir / "escaped_write_should_not_happen.txt"
    result = _call(code=f"open({str(target)!r}, 'w').write('escaped')")
    assert not target.exists()
    assert "Process exited with code" in result


@pytest.mark.skipif(not _sandbox_ready, reason="no sandbox backend installed on this machine")
def test_execute_python_denies_network_by_default() -> None:
    """A local loopback socket bind is used as the probe rather than a
    real outbound connection -- this project's sandbox profiles deny all
    networking (including local sockets), so this is self-contained and
    doesn't depend on the test runner having real internet access."""
    result = _call(
        code=(
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "s.bind(('127.0.0.1', 0))\n"
            "print('bound', s.getsockname())\n"
        )
    )
    assert "Process exited with code" in result
    assert "PermissionError" in result


@pytest.mark.skipif(not _sandbox_ready, reason="no sandbox backend installed on this machine")
def test_execute_python_allows_network_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "code_exec_network_access", True)
    result = _call(
        code=(
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "s.bind(('127.0.0.1', 0))\n"
            "print('bound ok')\n"
        )
    )
    assert "bound ok" in result


def test_socket_module_import_sanity() -> None:
    # Guards the network-probe snippets above: if the *test process*
    # itself can't create a UDP socket, the sandboxed-network assertions
    # would be meaningless (blocked for reasons unrelated to sandboxing).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.close()
