"""Owner-facing Google access catalog (#458)."""

from rivulets.integrations.google import SCOPE_DRIVE, SCOPE_DRIVE_READONLY, SCOPE_GMAIL_SEND
from rivulets.integrations.google_capabilities import (
    DEFAULT_CONNECT_CAPABILITIES,
    GoogleCapabilityError,
    capabilities_from_scopes,
    expand_capability_ids,
    missing_scopes,
    scopes_for_capabilities,
)


def test_default_connect_is_read_only() -> None:
    assert "gmail_read" in DEFAULT_CONNECT_CAPABILITIES
    assert "gmail_write" not in DEFAULT_CONNECT_CAPABILITIES
    scopes = scopes_for_capabilities(None)
    joined = " ".join(scopes)
    assert "gmail.readonly" in joined
    assert "gmail.send" not in joined
    assert "meetings.space.created" not in joined


def test_write_capability_pulls_in_read_sibling() -> None:
    names = expand_capability_ids(["gmail_write"])
    assert names == ("gmail_read", "gmail_write")
    scopes = scopes_for_capabilities(["gmail_write"])
    assert any(scope.endswith("gmail.readonly") for scope in scopes)
    assert any(scope.endswith("gmail.send") for scope in scopes)


def test_unknown_or_empty_capabilities_are_rejected() -> None:
    try:
        expand_capability_ids(["nope"])
    except GoogleCapabilityError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected GoogleCapabilityError")
    try:
        expand_capability_ids([])
    except GoogleCapabilityError as exc:
        assert "at least one" in str(exc)
    else:
        raise AssertionError("expected GoogleCapabilityError")


def test_drive_write_scope_satisfies_drive_read() -> None:
    assert missing_scopes((SCOPE_DRIVE,), (SCOPE_DRIVE_READONLY,)) == ()
    assert missing_scopes((), (SCOPE_DRIVE_READONLY,)) == (SCOPE_DRIVE_READONLY,)


def test_capabilities_from_stored_scopes() -> None:
    assert capabilities_from_scopes(
        ("https://www.googleapis.com/auth/gmail.readonly", "email")
    ) == ["gmail_read"]
    granted = capabilities_from_scopes((SCOPE_GMAIL_SEND, SCOPE_DRIVE))
    assert "gmail_write" in granted
    assert "drive_read" in granted
    assert "drive_write" in granted
