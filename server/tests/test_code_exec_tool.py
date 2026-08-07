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
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

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


def test_run_linux_builds_the_expected_command_and_denies_network_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """This machine doesn't necessarily have firejail (only exercised for
    real on Linux, per this file's own docstring) -- verifies the command
    _run_linux hands to subprocess.run is assembled correctly by faking
    both shutil.which and subprocess.run rather than actually shelling out."""
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(code_exec.subprocess, "run", fake_run)

    script_path = tmp_path / "script.py"
    script_path.write_text("print('hi')")

    result = code_exec._run_linux(  # pyright: ignore[reportPrivateUsage]
        script_path, tmp_path, allow_network=False
    )

    assert result.stdout == "ok"
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/firejail"
    assert "--net=none" in cmd
    assert str(script_path) in cmd
    assert captured["kwargs"]["cwd"] == tmp_path


def test_run_linux_omits_net_none_when_network_access_is_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(code_exec.subprocess, "run", fake_run)

    code_exec._run_linux(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "s.py", tmp_path, allow_network=True
    )

    assert "--net=none" not in captured["cmd"]


def test_execute_python_raises_timeout_error_when_the_sandbox_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_exec, "is_available", lambda: True)

    def fake_run_macos(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["sandbox-exec"], timeout=30)

    def fake_run_linux(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["firejail"], timeout=30)

    monkeypatch.setattr(code_exec, "_run_macos", fake_run_macos)
    monkeypatch.setattr(code_exec, "_run_linux", fake_run_linux)

    with pytest.raises(TimeoutError, match="30s sandbox timeout"):
        _call(code="import time; time.sleep(60)")


def test_socket_module_import_sanity() -> None:
    # Guards the network-probe snippets above: if the *test process*
    # itself can't create a UDP socket, the sandboxed-network assertions
    # would be meaningless (blocked for reasons unrelated to sandboxing).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.close()
