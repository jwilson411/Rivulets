"""Single source of truth for the running app's version.

Used by backup.py's pre-upgrade-snapshot check (security-and-dr.md) to
detect "the binary running now differs from the one that last started
here." Reads installed package metadata rather than hardcoding a string
that would need to stay in sync with pyproject.toml by hand.

Falls back to a literal if metadata isn't available — PyInstaller's onefile
build doesn't bundle dist-info by default, so a frozen binary would
otherwise raise PackageNotFoundError on every startup.
"""

from importlib.metadata import PackageNotFoundError, version


def _resolve_version() -> str:
    try:
        return version("rivulets")
    except PackageNotFoundError:
        return "0.1.0"


APP_VERSION = _resolve_version()
