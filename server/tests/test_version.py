"""APP_VERSION's fallback path (version.py) -- the PyInstaller onefile case
where package metadata isn't bundled."""

from importlib.metadata import PackageNotFoundError

import pytest

from rivulets import version


def test_resolve_version_returns_installed_package_version() -> None:
    assert version._resolve_version() == version.APP_VERSION  # pyright: ignore[reportPrivateUsage]


def test_resolve_version_falls_back_when_package_metadata_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_version(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version, "version", fake_version)
    assert version._resolve_version() == "unknown"  # pyright: ignore[reportPrivateUsage]
