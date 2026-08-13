"""Single source of truth for the running app's version.

Used by backup.py's pre-upgrade-snapshot check (security-and-dr.md) to
detect "the binary running now differs from the one that last started
here." Reads installed package metadata rather than hardcoding a string
that would need to stay in sync with pyproject.toml by hand.

packaging/_common.py's copy_metadata("rivulets") call bundles the
dist-info this needs into every frozen binary (#230) -- PyInstaller
doesn't include a package's own dist-info by default, so without that,
version("rivulets") would raise PackageNotFoundError on every startup of
a released binary.

The fallback below is only a safety net for that packaging step
regressing (or a from-source dev checkout with a stale/missing
dist-info), not the expected path. It deliberately isn't a
real-looking version: "unknown" fails _parse_version()'s dotted-integer
parse in update.py, so it can't compare as "older than every release"
and silently disable pre-upgrade backups or loop update.check_for_update
into re-downloading the same release forever.
"""

from importlib.metadata import PackageNotFoundError, version

_UNKNOWN_VERSION = "unknown"


def _resolve_version() -> str:
    try:
        return version("rivulets")
    except PackageNotFoundError:
        return _UNKNOWN_VERSION


APP_VERSION = _resolve_version()
