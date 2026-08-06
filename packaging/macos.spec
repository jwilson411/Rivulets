# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for macOS (x86_64, arm64).

Build with:
    uv run --group packaging pyinstaller packaging/macos.spec

Output name reflects the build host's architecture (both amd64 and arm64
builds run on macos-14 CI runners — this spec doesn't need to know which
in advance).

Code signing / notarization (Apple Developer ID) is not configured here
yet. Until then, users bypass Gatekeeper via right-click → Open.
"""

import platform
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

_arch = "arm64" if platform.machine() == "arm64" else "amd64"

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
    name=f"rivulets-darwin-{_arch}",
    console=True,
    strip=True,
    upx=False,
    codesign_identity=None,  # TODO(P1+): Apple Developer ID
    entitlements_file=None,
)
