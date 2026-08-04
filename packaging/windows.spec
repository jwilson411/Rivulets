# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Windows (x86_64) — see
docs/infrastructure/deployment-and-networking.md#build-matrix.

Build with:
    uv run --group packaging pyinstaller packaging/windows.spec

Output: dist/agent-hive-windows-amd64.exe (PyInstaller appends .exe itself).

EV code signing is P2+ per docs/infrastructure/cost-estimate.md — until
then, Windows SmartScreen will flag the binary and users click "More
info" -> "Run anyway".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))  # noqa: F821
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

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="agent-hive-windows-amd64",
    console=True,
    strip=False,  # strip isn't meaningful for PE binaries on Windows
    upx=False,
)
