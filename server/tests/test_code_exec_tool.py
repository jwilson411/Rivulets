"""execute_python builtin tool (tools/builtin/code_exec.py) — ADR-008 sandboxing.

is_available()/unavailable-path tests run on every platform/CI. The real
sandboxed-execution tests only run where this machine actually has the
backend ADR-008 calls for (sandbox-exec on macOS, firejail on Linux, the
AppContainer APIs on Windows) -- GitHub-hosted `ubuntu-latest` runners
have neither Unix backend installed by default, so those are skipped
there rather than failing the build, the same way this project already
tolerates other environment-dependent gaps (see tests/conftest.py's
in-memory keyring backend for the analogous CI gap on the credentials
side). The Windows backend IS exercised for real in CI: ci.yml's
`test-server-windows` job runs this file on a windows-latest runner, and
test_windows_runner_has_sandbox_support pins that the real tests can't
silently skip there.
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


@pytest.fixture(autouse=True)
def _clear_firejail_probe_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    # _firejail_works caches per-path for the process lifetime (right for
    # production, wrong across tests that fake different probe outcomes
    # for the same path).
    code_exec._firejail_works.cache_clear()  # pyright: ignore[reportPrivateUsage]


def _probe_returning(returncode: int) -> tuple[Callable[..., Any], dict[str, Any]]:
    """A fake subprocess.run for the firejail probe, capturing its cmd."""
    captured: dict[str, Any] = {"calls": 0}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["calls"] += 1
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=returncode, stdout=b"", stderr=b"")

    return fake_run, captured


def test_is_available_false_on_linux_without_firejail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning(None))
    assert is_available() is False


def test_is_available_true_on_linux_with_working_firejail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))
    fake_run, captured = _probe_returning(0)
    monkeypatch.setattr(code_exec.subprocess, "run", fake_run)
    assert is_available() is True
    # The probe must exercise the same sandbox setup a real invocation
    # uses -- binary presence alone proves nothing (see #516).
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/firejail"
    assert "--net=none" in cmd
    assert "--seccomp" in cmd
    assert "--caps.drop=all" in cmd


def test_is_available_false_on_linux_when_firejail_cannot_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Docker half-opt-in state (#516): the firejail binary is
    installed but the container withholds the privileges it needs, so
    every invocation fails. is_available() must report unavailable, not
    advertise a tool that can only fail."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))
    fake_run, _ = _probe_returning(1)
    monkeypatch.setattr(code_exec.subprocess, "run", fake_run)
    assert is_available() is False


def test_is_available_false_on_linux_when_the_probe_itself_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))

    def raising_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise OSError("exec format error")

    monkeypatch.setattr(code_exec.subprocess, "run", raising_run)
    assert is_available() is False


def test_firejail_probe_result_is_cached_per_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))
    fake_run, captured = _probe_returning(0)
    monkeypatch.setattr(code_exec.subprocess, "run", fake_run)
    assert is_available() is True
    assert is_available() is True
    assert captured["calls"] == 1


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


def test_is_available_on_windows_follows_backend_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows, availability is exactly code_exec_windows.is_supported()
    (AppContainer APIs + icacls present) -- true when the backend reports
    support, false when it doesn't."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(code_exec.code_exec_windows, "is_supported", lambda: True)
    assert is_available() is True
    monkeypatch.setattr(code_exec.code_exec_windows, "is_supported", lambda: False)
    assert is_available() is False


@pytest.mark.skipif(sys.platform == "win32", reason="asserts the non-Windows stub")
def test_windows_backend_reports_unsupported_off_windows() -> None:
    from rivulets.tools.builtin import code_exec_windows

    assert code_exec_windows.is_supported() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_windows_runner_has_sandbox_support() -> None:
    """On a stock Windows 10+ machine (including CI's windows-latest) the
    backend must report supported -- this pins that the real sandboxed
    tests below cannot all silently skip on the Windows CI job, which
    would leave NFR-3.5 claimed but unverified (#515)."""
    assert is_available() is True


def test_execute_python_refuses_to_run_unsandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core guarantee: if no sandbox backend is available, code must
    never fall back to running directly in this process."""
    monkeypatch.setattr(code_exec, "is_available", lambda: False)
    # match "sandbox" rather than a full message: which of the two
    # unavailable messages fires depends on this machine's platform and
    # whether a (non-functional) firejail happens to be installed.
    with pytest.raises(SandboxUnavailableError, match="sandbox"):
        _call(code="print('should never run')")


def test_execute_python_explains_installed_but_broken_firejail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Docker user who installed firejail without the opt-in container
    privileges (#516's persona) gets pointed at docs/security.md, not the
    generic 'install firejail' advice that would send them in a circle."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(code_exec.shutil, "which", _which_returning("/usr/bin/firejail"))
    fake_run, _ = _probe_returning(1)
    monkeypatch.setattr(code_exec.subprocess, "run", fake_run)
    with pytest.raises(SandboxUnavailableError, match="cannot create its sandbox"):
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


@pytest.mark.skipif(
    not (_sandbox_ready and sys.platform in ("darwin", "win32")),
    reason="macOS/Windows-only: firejail (Linux) replaces $HOME with sandbox_dir entirely, "
    "so this class of gap doesn't exist there (see code_exec.py's module docstring)",
)
def test_execute_python_cannot_read_workspace_dir_outside_the_sandbox_subpath() -> None:
    """On macOS, _macos_profile denies reads of workspace_dir except the
    tool_code_exec subpath -- regression test for a real bug caught by
    hand: workspace_dir under $TMPDIR (as it is in this very test suite,
    see conftest.py) lives under macOS's /var -> /private/var symlink,
    and a subpath rule built from the *unresolved* path silently never
    matched, leaving it fully readable. _macos_profile now .resolve()s
    every path it builds a rule from.

    On Windows the same guarantee falls out of the AppContainer model:
    user-profile/temp paths carry no ACE for the sandbox SID, and the
    only carve-outs granted are the sandbox dir + the Python install."""
    secret = get_settings().workspace_dir / "read_should_be_blocked.txt"
    secret.write_text("outside the sandbox subpath")

    result = _call(code=f"print(open({str(secret)!r}).read())")

    assert "outside the sandbox subpath" not in result
    assert "Process exited with code" in result
    assert "PermissionError" in result


@pytest.mark.skipif(
    not (_sandbox_ready and sys.platform != "win32"),
    reason="Unix-only probe: AppContainer loopback semantics differ on Windows -- "
    "see the win32-specific network tests below",
)
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


@pytest.mark.skipif(
    not (_sandbox_ready and sys.platform != "win32"),
    reason="Unix-only probe: AppContainer loopback semantics differ on Windows -- "
    "see the win32-specific network tests below",
)
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


# The Windows probes use a real outbound TCP connect instead of a loopback
# bind: an AppContainer blocks loopback unconditionally (even with network
# capabilities -- lifting that needs an admin-granted debugging exemption),
# so a loopback probe can't distinguish deny-by-default from the opt-in
# state there. github.com:443 is the one endpoint a GitHub-hosted runner
# is guaranteed to reach, making the deny test self-validating: without
# the sandbox the connect would succeed.
_WINDOWS_NETWORK_PROBE = (
    "import socket\n"
    "s = socket.create_connection(('github.com', 443), timeout=10)\n"
    "print('connected ok')\n"
)


@pytest.mark.skipif(not (_sandbox_ready and sys.platform == "win32"), reason="Windows-only probe")
def test_execute_python_denies_network_by_default_windows() -> None:
    result = _call(code=_WINDOWS_NETWORK_PROBE)
    assert "connected ok" not in result
    assert "Process exited with code" in result


@pytest.mark.skipif(not (_sandbox_ready and sys.platform == "win32"), reason="Windows-only probe")
def test_execute_python_allows_network_when_configured_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "code_exec_network_access", True)
    result = _call(code=_WINDOWS_NETWORK_PROBE)
    assert "connected ok" in result


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

    def fake_run_windows(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["appcontainer"], timeout=30)

    monkeypatch.setattr(code_exec, "_run_macos", fake_run_macos)
    monkeypatch.setattr(code_exec, "_run_linux", fake_run_linux)
    monkeypatch.setattr(code_exec, "_run_windows", fake_run_windows)

    with pytest.raises(TimeoutError, match="30s sandbox timeout"):
        _call(code="import time; time.sleep(60)")


def test_socket_module_import_sanity() -> None:
    # Guards the network-probe snippets above: if the *test process*
    # itself can't create a UDP socket, the sandboxed-network assertions
    # would be meaningless (blocked for reasons unrelated to sandboxing).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.close()
