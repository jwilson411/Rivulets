"""Shared path/config helpers for the platform .spec files.

Not a PyInstaller API wrapper — the `Analysis`/`PYZ`/`EXE` calls stay in
each .spec file since those names only exist in the namespace PyInstaller
injects when it execs a spec file directly. This module just centralizes
the bits that are identical across platforms (docs/infrastructure/
deployment-and-networking.md#binary-packaging-strategy) so linux.spec,
macos.spec, and windows.spec don't drift from each other.
"""

from pathlib import Path


def repo_root(specpath: str) -> Path:
    # SPECPATH (what callers pass here) is the *directory* containing the
    # spec file, per PyInstaller's docs — not the spec file path itself.
    return Path(specpath).resolve().parent


def entry_point(specpath: str) -> str:
    return str(repo_root(specpath) / "server" / "src" / "agent_hive" / "main.py")


def server_src(specpath: str) -> str:
    return str(repo_root(specpath) / "server" / "src")


def common_datas(specpath: str) -> list[tuple[str, str]]:
    root = repo_root(specpath)
    tools_dir = root / "server" / "src" / "agent_hive" / "tools" / "builtin"
    ui_build = root / "ui" / "build"

    datas = [(str(tools_dir), "agent_hive/tools/builtin")]
    if ui_build.exists():
        datas.append((str(ui_build), "agent_hive/static"))
    # else: `npm run build` in ui/ hasn't been run yet — the binary would
    # still work as an API-only server, just without a UI to serve.
    return datas


def common_hidden_imports() -> list[str]:
    # uvicorn's auto-detected loop/protocol implementations, SQLAlchemy's
    # DBAPI dialect plugins, and keyring's OS-specific backends are all
    # loaded dynamically (importlib / entry_points) rather than via a
    # `import` statement PyInstaller's static analysis can see. Verified
    # against a real `pyinstaller packaging/macos.spec` run + smoke test
    # (health endpoint) — the Linux/Windows keyring backends are included
    # on the assumption they have the same blind spot, not yet verified
    # on those platforms.
    return [
        "agno",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "aiosqlite",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.Windows",
        "keyring.backends.kwallet",
    ]


def common_excludes() -> list[str]:
    return ["tkinter", "test", "unittest", "pydoc_data"]
