"""Self-update for the packaged binary (issue #11).

Only applies to the PyInstaller onefile binary (`is_frozen`, same
`sys._MEIPASS` idiom app.py's `_static_dir` already uses) -- `uv run` and
the Docker image both run the plain Python package with no binary to
swap, so `UpdateStatus.applicable` is False for either and the UI/API
should offer no "Update now" action there (Docker users update via a new
image pull instead, per the issue's own notes).

Network failures here (offline, DNS, GitHub unreachable, no release
tagged yet) are swallowed, never raised -- matching the rest of the app's
offline-friendly posture (tools/builtin/http_request.py, config.py): a
failed background version check is "nothing new to report", not an error
state a user needs to see.
"""

import hashlib
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from rivulets.version import APP_VERSION

logger = logging.getLogger(__name__)

# Overridable the same way scripts/install.sh's RIVULETS_REPO env var is,
# for testing against a fork -- but read once at import time since (unlike
# install.sh, a fresh shell invocation each time) this module stays loaded
# for the life of the process. Ignored unless RIVULETS_ALLOW_REPO_OVERRIDE=1
# is also set (issue #245): otherwise a compromised environment variable
# alone (no filesystem/code access needed) could point self-update at an
# attacker-controlled fork whose "latest release" is a malicious binary --
# cosign verification below would even pass, since it's checked against
# whatever REPO ends up being.
_DEFAULT_REPO = "jwilson411/Rivulets"
_repo_override = os.environ.get("RIVULETS_REPO", _DEFAULT_REPO)
if _repo_override != _DEFAULT_REPO and os.environ.get("RIVULETS_ALLOW_REPO_OVERRIDE") != "1":
    logger.warning(
        "RIVULETS_REPO=%s ignored (set RIVULETS_ALLOW_REPO_OVERRIDE=1 to allow "
        "self-update from a non-default repo)",
        _repo_override,
    )
    _repo_override = _DEFAULT_REPO
REPO = _repo_override

_GITHUB_API_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 120.0
_COSIGN_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

# Set on the freshly-spawned process by _spawn_restarted_process, read (and
# consumed) by main.py before it binds -- see wait_for_port_free.
RESTART_ENV_VAR = "RIVULETS_UPDATE_RESTART"


class UpdateError(RuntimeError):
    """Base for apply_update failures the API layer maps to HTTP responses."""


class UpdateNotApplicableError(UpdateError):
    """Not a frozen binary, or an unrecognized platform/arch (no matching
    release.yml build)."""


class UpdateNotAvailableError(UpdateError):
    """apply_update called with no newer version to fetch."""


class UpdateVerificationError(UpdateError):
    """Downloaded binary failed checksum or cosign signature verification,
    or cosign isn't available on PATH to perform that verification."""


@dataclass(frozen=True)
class UpdateStatus:
    current_version: str
    latest_version: str | None
    update_available: bool
    applicable: bool
    asset_name: str | None
    download_url: str | None
    checksum_url: str | None


def is_frozen() -> bool:
    return getattr(sys, "_MEIPASS", None) is not None


_ASSET_OS = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}
_ASSET_ARCH = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}


def _platform_asset_name() -> str | None:
    """Matches release.yml's build matrix binary names exactly -- there's
    no windows-arm64 build, so that combination (like any other
    unrecognized os/arch pair) falls through to None."""
    os_name = _ASSET_OS.get(platform.system())
    arch = _ASSET_ARCH.get(platform.machine().lower())
    if os_name is None or arch is None:
        return None
    if os_name == "windows" and arch != "amd64":
        return None
    suffix = ".exe" if os_name == "windows" else ""
    return f"rivulets-{os_name}-{arch}{suffix}"


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """Release tags are "v0.2.0"-shaped (release.yml's `tags: ["v*"]`);
    APP_VERSION has no "v" prefix. Anything that isn't plain dotted
    integers (a pre-release suffix, a malformed tag) returns None rather
    than guessing -- comparing against an unparseable version could
    otherwise produce a false "update available" that then fails to
    download."""
    text = raw[1:] if raw.startswith("v") else raw
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:
        return None


async def check_for_update() -> UpdateStatus:
    asset_name = _platform_asset_name()
    applicable = is_frozen() and asset_name is not None
    latest_version: str | None = None
    download_url: str | None = None
    checksum_url: str | None = None

    try:
        async with httpx.AsyncClient(timeout=_GITHUB_API_TIMEOUT) as client:
            response = await client.get(
                f"https://api.github.com/repos/{REPO}/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "rivulets-update-check",
                },
            )
            if response.status_code != 404:
                response.raise_for_status()
                latest_version = response.json().get("tag_name")
    except httpx.HTTPError:
        logger.info("Update check failed (offline or GitHub unreachable)", exc_info=True)

    current = _parse_version(APP_VERSION)
    latest = _parse_version(latest_version) if latest_version else None
    update_available = current is not None and latest is not None and latest > current

    if update_available and asset_name is not None:
        base = f"https://github.com/{REPO}/releases/download/{latest_version}"
        download_url = f"{base}/{asset_name}"
        checksum_url = f"{download_url}.sha256"

    return UpdateStatus(
        current_version=APP_VERSION,
        latest_version=latest_version,
        update_available=update_available,
        applicable=applicable,
        asset_name=asset_name,
        download_url=download_url,
        checksum_url=checksum_url,
    )


async def _download_and_verify(
    client: httpx.AsyncClient, download_url: str, checksum_url: str, dest: Path
) -> None:
    checksum_response = await client.get(checksum_url)
    checksum_response.raise_for_status()
    # sha256sum(1)/shasum(1) output: "<hex digest>  <filename>" (release.yml's
    # Checksum step uses whichever of the two is on the runner).
    expected_digest = checksum_response.text.split()[0]

    binary_response = await client.get(download_url)
    binary_response.raise_for_status()
    digest = hashlib.sha256(binary_response.content).hexdigest()
    if digest != expected_digest:
        raise UpdateVerificationError(
            f"Downloaded binary checksum {digest} did not match published {expected_digest}"
        )
    dest.write_bytes(binary_response.content)
    dest.chmod(0o755)


def _cosign_available() -> bool:
    return shutil.which("cosign") is not None


async def _verify_cosign_signature(
    client: httpx.AsyncClient, download_url: str, binary_path: Path, sig_path: Path, cert_path: Path
) -> None:
    """Sigstore keyless verification against release.yml's own OIDC identity
    -- same check as scripts/install.sh's cosign step, but fails closed
    rather than degrading to checksum-only (issue #245): install.sh is a
    one-shot script a human reads before piping into `sh`, but this runs
    unattended inside an already-trusted process, so silently accepting an
    unsigned binary here is a much larger blast radius. A checksum alone
    only proves the download wasn't corrupted in transit -- it's fetched
    from the same GitHub Releases origin as the binary, so it proves
    nothing about a compromised release pipeline.
    """
    if not _cosign_available():
        raise UpdateVerificationError(
            "cosign is required to verify update signatures but was not found on PATH. "
            "Install cosign (https://docs.sigstore.dev/cosign/system_config/installation/) "
            "to enable self-update."
        )

    sig_response = await client.get(f"{download_url}.sig")
    sig_response.raise_for_status()
    cert_response = await client.get(f"{download_url}.pem")
    cert_response.raise_for_status()
    sig_path.write_bytes(sig_response.content)
    cert_path.write_bytes(cert_response.content)

    cosign_argv = [
        "cosign",
        "verify-blob",
        "--certificate",
        str(cert_path),
        "--signature",
        str(sig_path),
        "--certificate-identity-regexp",
        f"^https://github.com/{REPO}/.github/workflows/release.yml@.+$",
        "--certificate-oidc-issuer",
        _COSIGN_OIDC_ISSUER,
        str(binary_path),
    ]
    result = subprocess.run(cosign_argv, capture_output=True, text=True, check=False)  # noqa: S603, S607
    if result.returncode != 0:
        raise UpdateVerificationError(
            "cosign signature verification failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _swap_binaries(current_exe: Path, new_binary: Path) -> None:
    """Move the running binary aside, drop the verified download into its
    place. Same-directory renames only (new_binary is always written next
    to current_exe -- see apply_update), so this never crosses a
    filesystem boundary, which `os.rename` can't do atomically.

    Safe on a binary that's currently executing on every platform this
    ships for: POSIX keeps the running process's already-open inode valid
    regardless of what its directory entry gets renamed to or replaced by;
    Windows allows renaming (though not deleting) a file that's in use by
    a running image, which is all this does to the old path.
    """
    old_backup = current_exe.with_name(current_exe.name + ".old")
    old_backup.unlink(missing_ok=True)
    os.rename(current_exe, old_backup)
    os.rename(new_binary, current_exe)


def _spawn_restarted_process(current_exe: Path) -> None:
    """Hands off to a freshly-launched copy of the just-swapped-in binary,
    detached from this process's session/process group so it outlives this
    process exiting. RESTART_ENV_VAR tells that child (via main.py's
    wait_for_port_free) to wait for this process to actually release the
    listening port before it tries to bind the same one -- otherwise the
    two processes race for it and whichever loses crashes on startup.
    """
    env = {**os.environ, RESTART_ENV_VAR: "1"}
    argv = [str(current_exe), *sys.argv[1:]]
    if sys.platform == "win32":
        # getattr rather than a direct attribute reference: these two
        # constants only exist in the Windows build of the subprocess
        # module, so a direct `subprocess.DETACHED_PROCESS` would be a real
        # AttributeError under a test that exercises this branch (by
        # monkeypatching sys.platform) on the Linux/macOS runners this
        # suite actually runs on -- this branch is otherwise unreachable,
        # and therefore untestable, anywhere but real Windows.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(argv, env=env, creationflags=creationflags)  # noqa: S603
    else:
        subprocess.Popen(argv, env=env, start_new_session=True)  # noqa: S603


def wait_for_port_free(host: str, port: int, timeout: float = 30.0) -> None:
    """Called from main.py before uvicorn.run(), only when RESTART_ENV_VAR
    is set -- i.e. this process was just spawned by _spawn_restarted_process
    above, handing off from a binary self-update. Probes the target port
    until a bind succeeds or `timeout` elapses, so this process doesn't
    lose the race against the still-shutting-down old process for the same
    port.

    A small window remains between this probe releasing the port and
    uvicorn's own bind grabbing it -- the same reconnect-and-retry risk any
    two processes racing for a port share. Giving up silently after
    `timeout` and letting uvicorn's own bind error surface normally is
    safer than hanging forever if the old process never actually released
    it (e.g. something else already took the port).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((host, port))
        except OSError:
            time.sleep(0.2)
            continue
        finally:
            probe.close()
        return


def cleanup_stale_backup() -> None:
    """Best-effort removal of the previous binary a prior self-update left
    behind at `_swap_binaries`'s current_exe.old. Called on every startup
    (main.py), not just right after a restart -- a crash between the swap
    and a future cleanup would otherwise leave it behind indefinitely.
    Failing to remove it (e.g. still momentarily locked on Windows) isn't
    worth failing startup over; it'll just get retried next start."""
    if not is_frozen():
        return
    current_exe = Path(sys.executable).resolve()
    old_backup = current_exe.with_name(current_exe.name + ".old")
    try:
        old_backup.unlink(missing_ok=True)
    except OSError:
        logger.info("Could not remove stale update backup %s", old_backup, exc_info=True)


async def apply_update() -> None:
    status = await check_for_update()
    if not status.applicable:
        raise UpdateNotApplicableError(
            "Auto-update isn't available in this environment "
            "(not a packaged binary, or an unsupported platform/architecture)."
        )
    if not status.update_available or status.download_url is None or status.checksum_url is None:
        raise UpdateNotAvailableError("No update available.")

    current_exe = Path(sys.executable).resolve()
    new_binary = current_exe.with_name(current_exe.name + ".new")
    sig_path = current_exe.with_name(current_exe.name + ".sig")
    cert_path = current_exe.with_name(current_exe.name + ".pem")
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        await _download_and_verify(client, status.download_url, status.checksum_url, new_binary)
        try:
            await _verify_cosign_signature(
                client, status.download_url, new_binary, sig_path, cert_path
            )
        except UpdateVerificationError:
            new_binary.unlink(missing_ok=True)
            raise
        finally:
            sig_path.unlink(missing_ok=True)
            cert_path.unlink(missing_ok=True)
    _swap_binaries(current_exe, new_binary)
    _spawn_restarted_process(current_exe)
