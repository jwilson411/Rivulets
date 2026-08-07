# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Linux (x86_64, aarch64).

Build with:
    uv run --group packaging pyinstaller packaging/linux.spec

Output name reflects the build host's architecture (both amd64 and arm64
builds run their own runner in CI — see release.yml's matrix — so this
spec doesn't need to know which in advance).
"""

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))  # noqa: F821 — SPECPATH is injected by PyInstaller
from _common import (  # noqa: E402
    common_datas,
    common_excludes,
    common_hidden_imports,
    entry_point,
    server_src,
)

# platform.machine() on Linux reports "aarch64"/"x86_64", not the
# "arm64"/"amd64" naming this project's release assets use elsewhere.
_arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"

a = Analysis(  # noqa: F821
    [entry_point(SPECPATH)],  # noqa: F821
    pathex=[server_src(SPECPATH)],  # noqa: F821
    datas=common_datas(SPECPATH),  # noqa: F821
    hiddenimports=common_hidden_imports(),
    excludes=common_excludes(),
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

# Passing a.binaries/a.datas directly to EXE (rather than via COLLECT) is
# what makes this a --onefile-equivalent single executable.
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name=f"rivulets-linux-{_arch}",
    console=True,
    strip=True,
    upx=False,
)
