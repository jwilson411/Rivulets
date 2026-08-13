"""Code Execution built-in tool (FR-8.1, NFR-3.5, ADR-008).

Runs agent-submitted Python inside a platform-native sandbox rather than
directly in this process: firejail on Linux, `sandbox-exec` on macOS.
Both backends give the same two guarantees:

  - Filesystem writes are confined to a workspace-bounded directory
    (`<workspace_dir>/tool_code_exec`). Reads are mostly left open — the
    sandboxed interpreter still needs to read Python itself, the
    stdlib, and installed site-packages to start at all, so full read
    isolation isn't attempted (there's no fixed set of paths that
    covers every real Python install layout without risking silently
    breaking code execution). On macOS, `_macos_profile` layers
    best-effort deny rules on top for the highest-value targets this
    app can reason about completely: its own workspace_dir (the SQLite
    DB, encrypted credential fallback store) and common credential
    locations under $HOME (~/.ssh, ~/.aws, ~/.gnupg, ~/.docker,
    ~/.kube, ~/.netrc) — not a completeness guarantee, just defense in
    depth. On Linux, firejail's `--private=<dir>` (below) already
    replaces $HOME with sandbox_dir entirely, so this class of gap
    doesn't exist there. Combined with network access being denied by
    default (below), what remains readable isn't an exfiltration path
    unless RIVULETS_CODE_EXEC_NETWORK_ACCESS=1 is also set.
  - Outbound network access is denied by default (NFR-3.5). Set
    RIVULETS_CODE_EXEC_NETWORK_ACCESS=1 (config.py) to allow it
    workspace-wide — there's no per-run toggle.

Windows has no sandbox wired up. ADR-008 originally proposed Windows job
objects, but a job object alone only bounds process/resource usage — it
doesn't restrict filesystem or network access, so it can't actually meet
NFR-3.5's MUST requirements on its own. Doing that properly needs a
restricted token plus Windows Filtering Platform firewall rules, which
is real, unwritten, unverified-on-this-project work. Rather than ship
something that *looks* sandboxed but doesn't enforce the requirement,
`is_available()` reports the tool unavailable on Windows until that
lands — api/tools.py surfaces this so an agent can't be handed a tool
that will only fail the first time it's actually invoked (the same
"unavailable" pattern NFR-2.4 already uses for unreachable model
providers). The same applies on Linux/macOS if the required sandbox
binary isn't installed — including the published Docker image, which
does not install firejail (see Dockerfile's runtime-stage comment and
docs/security.md for why, and the opt-in profile for installs that want
it anyway).
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agno.tools import tool

from rivulets.config import get_settings

# Best-effort read denials layered on top of macOS's otherwise-unrestricted
# (allow file-read*) -- see _macos_profile's docstring for why a full
# allowlist isn't feasible. Covers this app's own workspace (the SQLite DB,
# encrypted credential fallback store, sync state -- all higher-value than
# generic dotfiles, and a set this module can reason about completely) plus
# the handful of credential locations most likely to sit under $HOME.
_SENSITIVE_HOME_DIRS = (".ssh", ".aws", ".gnupg", ".docker", ".kube")
_SENSITIVE_HOME_FILES = (".netrc",)

_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_CHARS = 20_000


class SandboxUnavailableError(RuntimeError):
    pass


def _sandbox_root() -> Path:
    root = get_settings().workspace_dir / "tool_code_exec"
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_available() -> bool:
    """Whether this platform has a working sandbox backend installed."""
    if sys.platform == "linux":
        return shutil.which("firejail") is not None
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    return False


def _macos_profile(sandbox_dir: Path, allow_network: bool) -> str:
    network_rule = "(allow network*)" if allow_network else "(deny network*)"
    # (allow file-read*): full read isolation isn't attempted -- the
    # sandboxed interpreter still needs to read Python itself, the
    # stdlib, and installed site-packages to start at all, and those
    # can live anywhere (a pyenv/uv install under $HOME, a venv next to
    # a from-source checkout, etc.), so there's no fixed allowlist that
    # covers every real deployment without risking silently breaking
    # code execution. Instead, deny rules below carve out the specific,
    # known-sensitive locations most worth protecting; Seatbelt resolves
    # a read against the *most specific* matching subpath rule, so a
    # deny on workspace_dir is overridden by the narrower allow on
    # sandbox_dir (a subdirectory of it) below. This is defense in
    # depth, not a completeness guarantee -- see the module docstring's
    # RIVULETS_CODE_EXEC_NETWORK_ACCESS note.
    # .resolve() everything below: macOS's sandbox kernel extension
    # matches against the *canonical* (symlinks-resolved) path, and
    # several common roots are themselves symlinks on macOS (/tmp ->
    # /private/tmp, /var -> /private/var -- the latter matters because
    # $TMPDIR, and therefore any workspace_dir pointed under it, lives
    # under /var/folders/...). A subpath rule built from the unresolved
    # form silently never matches in that case -- confirmed by hand: an
    # unresolved-path profile with workspace_dir under $TMPDIR left it
    # fully readable despite the (deny ...) rule being present.
    sandbox_dir = sandbox_dir.resolve()
    home = Path.home().resolve()
    deny_dirs = [
        get_settings().workspace_dir.resolve(),
        *(home / name for name in _SENSITIVE_HOME_DIRS),
    ]
    deny_files = [home / name for name in _SENSITIVE_HOME_FILES]
    deny_rules = "\n".join(
        [
            *(f'(deny file-read* (subpath "{p}"))' for p in deny_dirs),
            *(f'(deny file-read* (literal "{p}"))' for p in deny_files),
        ]
    )
    # mach-lookup/sysctl-read/iokit-open: the CPython interpreter itself
    # needs these to start up on macOS (thread/clock/entropy services);
    # without them the sandboxed process fails before running any
    # agent-submitted code at all.
    return f"""(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow file-read*)
{deny_rules}
(allow file-read* (subpath "{sandbox_dir}"))
(allow file-write* (subpath "{sandbox_dir}"))
(allow file-write-data (literal "/dev/null"))
(allow file-write-data (literal "/dev/dtracehelper"))
(allow sysctl-read)
(allow mach-lookup)
(allow iokit-open)
{network_rule}
"""


def _run_macos(
    script_path: Path, sandbox_dir: Path, allow_network: bool
) -> subprocess.CompletedProcess[str]:
    profile = _macos_profile(sandbox_dir, allow_network)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sb", dir=sandbox_dir, delete=False, encoding="utf-8"
    ) as profile_file:
        profile_file.write(profile)
        profile_path = Path(profile_file.name)
    sandbox_exec = shutil.which("sandbox-exec")
    assert sandbox_exec is not None  # is_available() already confirmed this
    try:
        # Every argument here is built by this function from trusted
        # inputs (sandbox_exec/sys.executable resolved by this process,
        # profile_path/script_path files this function just created) —
        # `code` (the actual untrusted agent input) only ever reaches
        # script_path's file contents, never a subprocess argument.
        return subprocess.run(  # noqa: S603
            [sandbox_exec, "-f", str(profile_path), sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            cwd=sandbox_dir,
        )
    finally:
        profile_path.unlink(missing_ok=True)


def _run_linux(
    script_path: Path, sandbox_dir: Path, allow_network: bool
) -> subprocess.CompletedProcess[str]:
    # --private=<dir>: bind-mounts sandbox_dir as the sandboxed process's
    # $HOME and hides the rest of the real filesystem's writable paths
    # (combined with --private-tmp/--private-dev, per firejail's own
    # sandboxing model — see ADR-008).
    # --caps.drop=all/--nonewprivs/--noroot: least-privilege process,
    # can't escalate or use any Linux capability.
    # --seccomp: firejail's default syscall filter.
    firejail = shutil.which("firejail")
    assert firejail is not None  # is_available() already confirmed this
    net_args = [] if allow_network else ["--net=none"]
    cmd = [
        firejail,
        "--quiet",
        "--noprofile",
        f"--private={sandbox_dir}",
        "--private-tmp",
        "--private-dev",
        "--seccomp",
        "--caps.drop=all",
        "--nonewprivs",
        "--noroot",
        *net_args,
        sys.executable,
        str(script_path),
    ]
    # See _run_macos's comment above: every argument is built from
    # trusted inputs, `code` only ever reaches script_path's contents.
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, cwd=sandbox_dir
    )


@tool
def execute_python(code: str) -> str:
    """Execute Python code in a sandboxed environment (filesystem writes
    confined to a workspace directory, network denied by default) and
    return its combined stdout/stderr."""
    if not is_available():
        raise SandboxUnavailableError(
            f"No sandbox backend available on this platform ({sys.platform}). "
            "Install firejail (Linux) or use macOS (sandbox-exec ships with the "
            "OS) to enable code execution."
        )

    sandbox_dir = _sandbox_root()
    allow_network = get_settings().code_exec_network_access

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", dir=sandbox_dir, delete=False, encoding="utf-8"
    ) as script_file:
        script_file.write(code)
        script_path = Path(script_file.name)

    try:
        run = _run_macos if sys.platform == "darwin" else _run_linux
        result = run(script_path, sandbox_dir, allow_network)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"Code execution exceeded the {_TIMEOUT_SECONDS}s sandbox timeout"
        ) from exc
    finally:
        script_path.unlink(missing_ok=True)

    output = (result.stdout + result.stderr)[:_MAX_OUTPUT_CHARS]
    if result.returncode != 0:
        return f"Process exited with code {result.returncode}\n{output}"
    return output
