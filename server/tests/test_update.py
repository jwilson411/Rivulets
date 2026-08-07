"""update.py -- self-update core logic (issue #11).

httpx calls are routed through MockTransport rather than the network
(same pattern as test_web_search_tool.py / test_http_request_tool.py, just
for the async client update.py actually uses). subprocess.Popen and the
platform/frozen detectors are monkeypatched so these never touch a real
process or a real PyInstaller build.

This whole file exercises update.py's internals directly (there's no
public API surface separate from them worth testing through instead), so
every `update._foo` reference below is deliberate, not a leak -- see the
reportPrivateUsage override below.
"""

# pyright: reportPrivateUsage=false

import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from rivulets import update


def _mock_async_client_factory(handler: Any) -> type[httpx.AsyncClient]:
    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    return _MockAsyncClient


def _release_json_handler(tag_name: str, asset_name: str) -> Any:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{update.REPO}/releases/latest"
        return httpx.Response(200, json={"tag_name": tag_name, "assets": [{"name": asset_name}]})

    return handler


class TestPlatformAssetName:
    def test_known_platforms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cases = [
            ("Linux", "x86_64", "rivulets-linux-amd64"),
            ("Linux", "aarch64", "rivulets-linux-arm64"),
            ("Darwin", "x86_64", "rivulets-darwin-amd64"),
            ("Darwin", "arm64", "rivulets-darwin-arm64"),
            ("Windows", "AMD64", "rivulets-windows-amd64.exe"),
        ]
        for system, machine, expected in cases:
            monkeypatch.setattr(update.platform, "system", lambda s=system: s)
            monkeypatch.setattr(update.platform, "machine", lambda m=machine: m)
            assert update._platform_asset_name() == expected  # noqa: SLF001

    def test_unknown_os_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update.platform, "system", lambda: "FreeBSD")
        monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
        assert update._platform_asset_name() is None  # noqa: SLF001

    def test_unknown_arch_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update.platform, "system", lambda: "Linux")
        monkeypatch.setattr(update.platform, "machine", lambda: "riscv64")
        assert update._platform_asset_name() is None  # noqa: SLF001

    def test_windows_arm64_has_no_release_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(update.platform, "system", lambda: "Windows")
        monkeypatch.setattr(update.platform, "machine", lambda: "ARM64")
        assert update._platform_asset_name() is None  # noqa: SLF001


class TestParseVersion:
    def test_strips_v_prefix(self) -> None:
        assert update._parse_version("v1.2.3") == (1, 2, 3)  # noqa: SLF001

    def test_bare_version(self) -> None:
        assert update._parse_version("0.1.0") == (0, 1, 0)  # noqa: SLF001

    def test_non_numeric_returns_none(self) -> None:
        assert update._parse_version("v1.2.0-rc1") is None  # noqa: SLF001


class TestIsFrozen:
    def test_false_without_meipass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        assert update.is_frozen() is False

    def test_true_with_meipass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "_MEIPASS", "meipass-sentinel", raising=False)
        assert update.is_frozen() is True


@pytest.fixture
def frozen_linux_amd64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", "meipass-sentinel", raising=False)
    monkeypatch.setattr(update.platform, "system", lambda: "Linux")
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")


class TestCheckForUpdate:
    async def test_update_available(
        self, monkeypatch: pytest.MonkeyPatch, frozen_linux_amd64: None
    ) -> None:
        monkeypatch.setattr(update, "APP_VERSION", "0.1.0")
        monkeypatch.setattr(
            update.httpx,
            "AsyncClient",
            _mock_async_client_factory(_release_json_handler("v0.2.0", "rivulets-linux-amd64")),
        )

        status = await update.check_for_update()

        assert status.current_version == "0.1.0"
        assert status.latest_version == "v0.2.0"
        assert status.update_available is True
        assert status.applicable is True
        assert status.asset_name == "rivulets-linux-amd64"
        expected_download_url = (
            f"https://github.com/{update.REPO}/releases/download/v0.2.0/rivulets-linux-amd64"
        )
        assert status.download_url == expected_download_url
        assert status.checksum_url == expected_download_url + ".sha256"

    async def test_already_up_to_date(
        self, monkeypatch: pytest.MonkeyPatch, frozen_linux_amd64: None
    ) -> None:
        monkeypatch.setattr(update, "APP_VERSION", "0.2.0")
        monkeypatch.setattr(
            update.httpx,
            "AsyncClient",
            _mock_async_client_factory(_release_json_handler("v0.2.0", "rivulets-linux-amd64")),
        )

        status = await update.check_for_update()

        assert status.update_available is False
        assert status.download_url is None
        assert status.checksum_url is None

    async def test_no_releases_published_yet_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch, frozen_linux_amd64: None
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        monkeypatch.setattr(update.httpx, "AsyncClient", _mock_async_client_factory(handler))

        status = await update.check_for_update()

        assert status.latest_version is None
        assert status.update_available is False

    async def test_network_failure_is_silent(
        self, monkeypatch: pytest.MonkeyPatch, frozen_linux_amd64: None
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        monkeypatch.setattr(update.httpx, "AsyncClient", _mock_async_client_factory(handler))

        status = await update.check_for_update()

        assert status.latest_version is None
        assert status.update_available is False

    async def test_not_applicable_when_not_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(update.platform, "system", lambda: "Linux")
        monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(update, "APP_VERSION", "0.1.0")
        monkeypatch.setattr(
            update.httpx,
            "AsyncClient",
            _mock_async_client_factory(_release_json_handler("v0.2.0", "rivulets-linux-amd64")),
        )

        status = await update.check_for_update()

        assert status.update_available is True
        assert status.applicable is False


class TestDownloadAndVerify:
    async def test_matching_checksum_writes_executable_file(self, tmp_path: Path) -> None:
        content = b"fake binary contents"
        digest = update.hashlib.sha256(content).hexdigest()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".sha256"):
                return httpx.Response(200, text=f"{digest}  rivulets-linux-amd64\n")
            return httpx.Response(200, content=content)

        dest = tmp_path / "rivulets.new"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await update._download_and_verify(  # noqa: SLF001
                client, "https://example.com/bin", "https://example.com/bin.sha256", dest
            )

        assert dest.read_bytes() == content
        assert dest.stat().st_mode & 0o111

    async def test_mismatched_checksum_raises_and_does_not_write(self, tmp_path: Path) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".sha256"):
                return httpx.Response(200, text="0" * 64 + "  rivulets-linux-amd64\n")
            return httpx.Response(200, content=b"fake binary contents")

        dest = tmp_path / "rivulets.new"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(update.UpdateVerificationError):
                await update._download_and_verify(  # noqa: SLF001
                    client, "https://example.com/bin", "https://example.com/bin.sha256", dest
                )

        assert not dest.exists()


class TestSwapBinaries:
    def test_moves_old_aside_and_promotes_new(self, tmp_path: Path) -> None:
        current = tmp_path / "rivulets"
        current.write_bytes(b"old version")
        new_binary = tmp_path / "rivulets.new"
        new_binary.write_bytes(b"new version")

        update._swap_binaries(current, new_binary)  # noqa: SLF001

        assert current.read_bytes() == b"new version"
        old_backup = tmp_path / "rivulets.old"
        assert old_backup.read_bytes() == b"old version"
        assert not new_binary.exists()

    def test_removes_stale_old_backup_first(self, tmp_path: Path) -> None:
        current = tmp_path / "rivulets"
        current.write_bytes(b"v2")
        new_binary = tmp_path / "rivulets.new"
        new_binary.write_bytes(b"v3")
        stale = tmp_path / "rivulets.old"
        stale.write_bytes(b"v1 (stale)")

        update._swap_binaries(current, new_binary)  # noqa: SLF001

        assert stale.read_bytes() == b"v2"


class TestSpawnRestartedProcess:
    def test_posix_uses_start_new_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        calls: list[tuple[Any, Any]] = []

        def fake_popen(argv: Any, **kwargs: Any) -> None:
            calls.append((argv, kwargs))

        monkeypatch.setattr(update.subprocess, "Popen", fake_popen)

        exe = tmp_path / "rivulets"
        update._spawn_restarted_process(exe)  # noqa: SLF001

        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[0] == str(exe)
        assert kwargs["start_new_session"] is True
        assert kwargs["env"][update.RESTART_ENV_VAR] == "1"

    def test_windows_uses_creationflags(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        calls: list[tuple[Any, Any]] = []

        def fake_popen(argv: Any, **kwargs: Any) -> None:
            calls.append((argv, kwargs))

        monkeypatch.setattr(update.subprocess, "Popen", fake_popen)

        exe = tmp_path / "rivulets.exe"
        update._spawn_restarted_process(exe)  # noqa: SLF001

        assert len(calls) == 1
        _, kwargs = calls[0]
        assert "creationflags" in kwargs
        assert "start_new_session" not in kwargs


class TestWaitForPortFree:
    def test_returns_once_port_released(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.listen(1)

        def release_later() -> None:
            time.sleep(0.15)
            sock.close()

        thread = threading.Thread(target=release_later)
        thread.start()
        started = time.monotonic()
        update.wait_for_port_free("127.0.0.1", port, timeout=5.0)
        elapsed = time.monotonic() - started
        thread.join()

        assert elapsed < 5.0

    def test_gives_up_after_timeout(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.listen(1)
        try:
            started = time.monotonic()
            update.wait_for_port_free("127.0.0.1", port, timeout=0.3)
            elapsed = time.monotonic() - started
            assert elapsed >= 0.3
        finally:
            sock.close()


class TestCleanupStaleBackup:
    def test_noop_when_not_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        update.cleanup_stale_backup()  # must not raise even with no exe/backup present

    def test_removes_stale_backup_when_frozen(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        exe = tmp_path / "rivulets"
        exe.write_bytes(b"current")
        old_backup = tmp_path / "rivulets.old"
        old_backup.write_bytes(b"stale")

        monkeypatch.setattr(sys, "_MEIPASS", "meipass-sentinel", raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))

        update.cleanup_stale_backup()

        assert not old_backup.exists()

    def test_missing_backup_is_a_noop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        exe = tmp_path / "rivulets"
        exe.write_bytes(b"current")

        monkeypatch.setattr(sys, "_MEIPASS", "meipass-sentinel", raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))

        update.cleanup_stale_backup()  # must not raise

    def test_oserror_is_swallowed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        exe = tmp_path / "rivulets"
        exe.write_bytes(b"current")
        # A directory in place of the expected file: unlink() raises
        # IsADirectoryError (an OSError), exercising the swallow-and-log path
        # without needing to mock Path.unlink directly.
        old_backup = tmp_path / "rivulets.old"
        old_backup.mkdir()

        monkeypatch.setattr(sys, "_MEIPASS", "meipass-sentinel", raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))

        update.cleanup_stale_backup()  # must not raise

        assert old_backup.exists()  # a directory isn't touched by unlink's failure


class TestApplyUpdate:
    async def test_raises_when_not_applicable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(update.platform, "system", lambda: "Linux")
        monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(
            update.httpx,
            "AsyncClient",
            _mock_async_client_factory(_release_json_handler("v99.0.0", "rivulets-linux-amd64")),
        )

        with pytest.raises(update.UpdateNotApplicableError):
            await update.apply_update()

    async def test_raises_when_no_update_available(
        self, monkeypatch: pytest.MonkeyPatch, frozen_linux_amd64: None
    ) -> None:
        monkeypatch.setattr(update, "APP_VERSION", "99.0.0")
        monkeypatch.setattr(
            update.httpx,
            "AsyncClient",
            _mock_async_client_factory(_release_json_handler("v0.2.0", "rivulets-linux-amd64")),
        )

        with pytest.raises(update.UpdateNotAvailableError):
            await update.apply_update()

    async def test_full_flow_downloads_swaps_and_spawns(
        self, monkeypatch: pytest.MonkeyPatch, frozen_linux_amd64: None, tmp_path: Path
    ) -> None:
        content = b"new fake binary"
        digest = update.hashlib.sha256(content).hexdigest()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == f"/repos/{update.REPO}/releases/latest":
                return httpx.Response(
                    200, json={"tag_name": "v0.2.0", "assets": [{"name": "rivulets-linux-amd64"}]}
                )
            if request.url.path.endswith(".sha256"):
                return httpx.Response(200, text=f"{digest}  rivulets-linux-amd64\n")
            return httpx.Response(200, content=content)

        monkeypatch.setattr(update, "APP_VERSION", "0.1.0")
        monkeypatch.setattr(update.httpx, "AsyncClient", _mock_async_client_factory(handler))

        exe = tmp_path / "rivulets"
        exe.write_bytes(b"old fake binary")
        monkeypatch.setattr(sys, "executable", str(exe))

        spawned: list[Path] = []

        def fake_spawn(current_exe: Path) -> None:
            spawned.append(current_exe)

        monkeypatch.setattr(update, "_spawn_restarted_process", fake_spawn)

        await update.apply_update()

        assert exe.read_bytes() == content
        assert (tmp_path / "rivulets.old").read_bytes() == b"old fake binary"
        assert spawned == [exe.resolve()]
