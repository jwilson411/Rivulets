"""Shared path/config helpers for the platform .spec files.

Not a PyInstaller API wrapper — the `Analysis`/`PYZ`/`EXE` calls stay in
each .spec file since those names only exist in the namespace PyInstaller
injects when it execs a spec file directly. This module just centralizes
the bits that are identical across platforms so linux.spec, macos.spec,
and windows.spec don't drift from each other.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


def repo_root(specpath: str) -> Path:
    # SPECPATH (what callers pass here) is the *directory* containing the
    # spec file, per PyInstaller's docs — not the spec file path itself.
    return Path(specpath).resolve().parent


def entry_point(specpath: str) -> str:
    return str(repo_root(specpath) / "server" / "src" / "rivulets" / "main.py")


def server_src(specpath: str) -> str:
    return str(repo_root(specpath) / "server" / "src")


def common_datas(specpath: str) -> list[tuple[str, str]]:
    root = repo_root(specpath)
    tools_dir = root / "server" / "src" / "rivulets" / "tools" / "builtin"
    ui_build = root / "ui" / "build"

    datas = [(str(tools_dir), "rivulets/tools/builtin")]
    if ui_build.exists():
        datas.append((str(ui_build), "rivulets/static"))
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
    #
    # coincurve._cffi_backend (sync/'s libp2p -> coincurve dependency
    # chain, for secp256k1 peer identity keys) is the same category of
    # gap: cffi>=2.0 wheels vendor a *private*, package-namespaced copy
    # of the generic cffi runtime extension (coincurve/_cffi_backend.*.so,
    # not the top-level `_cffi_backend` PyInstaller's cffi hook already
    # knows about) that coincurve._libsecp256k1's compiled init code
    # imports from C, invisible to modulegraph's static analysis.
    # Reproduced locally via a real `pyinstaller packaging/macos.spec`
    # build + run (ModuleNotFoundError: No module named
    # 'coincurve._cffi_backend'), matching the CI smoke test failure on
    # Linux — confirmed fixed by adding this hidden import and rebuilding.
    #
    # multiaddr.codecs.*: multiaddr.codecs.codec_by_name() resolves each
    # address-component codec (ip4, ip6, cid, onion, ...) via
    # importlib.import_module(f"multiaddr.codecs.{name}") — a runtime
    # string lookup, same blind spot as everything above. Rather than
    # hand-list every codec module (and silently drift as multiaddr adds
    # more), collect_submodules pulls in whatever the installed version
    # actually ships. Also reproduced + confirmed fixed locally, one
    # layer deeper in the same libp2p/sync import chain as the
    # coincurve fix above (ModuleNotFoundError: No module named
    # 'multiaddr.codecs.ip4').
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
        "coincurve._cffi_backend",
        *collect_submodules("multiaddr.codecs"),
    ]


def common_excludes() -> list[str]:
    return ["tkinter", "test", "unittest", "pydoc_data"]
