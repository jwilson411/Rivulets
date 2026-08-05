# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Linux (x86_64, aarch64) — see
docs/infrastructure/deployment-and-networking.md#build-matrix.

Build with:
    uv run --group packaging pyinstaller packaging/linux.spec

Output: dist/rivulets-linux-amd64 (or -arm64, per build host).
"""

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
    name="rivulets-linux-amd64",
    console=True,
    strip=True,
    upx=False,
)
