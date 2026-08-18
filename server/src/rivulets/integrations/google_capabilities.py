"""Owner-facing Google access boxes (#458).

Connect used to request every Workspace scope at once, including send
and Drive write. The owner now picks surfaces; read is the default so
Gmail can be connected without send-as-me. Tools still need the matching
Rivulets grant (`integrations:google` / `integrations:google:write`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from rivulets.integrations.google import (
    SCOPE_CALENDAR_EVENTS,
    SCOPE_CALENDAR_READONLY,
    SCOPE_CONTACTS_OTHER_READONLY,
    SCOPE_CONTACTS_READONLY,
    SCOPE_DOCS,
    SCOPE_DOCS_READONLY,
    SCOPE_DRIVE,
    SCOPE_DRIVE_READONLY,
    SCOPE_EMAIL,
    SCOPE_GMAIL_COMPOSE,
    SCOPE_GMAIL_READONLY,
    SCOPE_GMAIL_SEND,
    SCOPE_MEET_SPACE_CREATED,
    SCOPE_OPENID,
    SCOPE_SHEETS,
    SCOPE_SHEETS_READONLY,
    SCOPE_TASKS,
    SCOPE_TASKS_READONLY,
)

IDENTITY_SCOPES: tuple[str, ...] = (SCOPE_OPENID, SCOPE_EMAIL)

_WRITE_TO_READ: dict[str, str] = {
    "gmail_write": "gmail_read",
    "calendar_write": "calendar_read",
    "drive_write": "drive_read",
    "docs_write": "docs_read",
    "sheets_write": "sheets_read",
    "tasks_write": "tasks_read",
}

# A broader write scope satisfies its readonly sibling.
_SCOPE_IMPLIED_BY: dict[str, tuple[str, ...]] = {
    SCOPE_DRIVE_READONLY: (SCOPE_DRIVE,),
    SCOPE_DOCS_READONLY: (SCOPE_DOCS,),
    SCOPE_SHEETS_READONLY: (SCOPE_SHEETS,),
    SCOPE_TASKS_READONLY: (SCOPE_TASKS,),
    SCOPE_CALENDAR_READONLY: (SCOPE_CALENDAR_EVENTS,),
}


class GoogleCapabilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GoogleCapability:
    id: str
    group: str
    group_label: str
    label: str
    write: bool
    scopes: tuple[str, ...]


GOOGLE_CAPABILITIES: tuple[GoogleCapability, ...] = (
    GoogleCapability("gmail_read", "gmail", "Gmail", "read", False, (SCOPE_GMAIL_READONLY,)),
    GoogleCapability(
        "gmail_write",
        "gmail",
        "Gmail",
        "send and draft",
        True,
        (SCOPE_GMAIL_COMPOSE, SCOPE_GMAIL_SEND),
    ),
    GoogleCapability(
        "calendar_read",
        "calendar",
        "Calendar",
        "read",
        False,
        (SCOPE_CALENDAR_READONLY,),
    ),
    GoogleCapability(
        "calendar_write",
        "calendar",
        "Calendar",
        "create and update",
        True,
        (SCOPE_CALENDAR_EVENTS,),
    ),
    GoogleCapability("drive_read", "drive", "Drive", "read", False, (SCOPE_DRIVE_READONLY,)),
    GoogleCapability("drive_write", "drive", "Drive", "write", True, (SCOPE_DRIVE,)),
    GoogleCapability("docs_read", "docs", "Docs", "read", False, (SCOPE_DOCS_READONLY,)),
    GoogleCapability("docs_write", "docs", "Docs", "append", True, (SCOPE_DOCS,)),
    GoogleCapability("sheets_read", "sheets", "Sheets", "read", False, (SCOPE_SHEETS_READONLY,)),
    GoogleCapability("sheets_write", "sheets", "Sheets", "update", True, (SCOPE_SHEETS,)),
    GoogleCapability(
        "contacts_read",
        "contacts",
        "Contacts",
        "search",
        False,
        (SCOPE_CONTACTS_READONLY, SCOPE_CONTACTS_OTHER_READONLY),
    ),
    GoogleCapability("tasks_read", "tasks", "Tasks", "list", False, (SCOPE_TASKS_READONLY,)),
    GoogleCapability("tasks_write", "tasks", "Tasks", "add", True, (SCOPE_TASKS,)),
    GoogleCapability(
        "meet_write",
        "meet",
        "Meet",
        "create links",
        True,
        (SCOPE_MEET_SPACE_CREATED,),
    ),
)

GOOGLE_CAPABILITY_BY_ID: dict[str, GoogleCapability] = {cap.id: cap for cap in GOOGLE_CAPABILITIES}

DEFAULT_CONNECT_CAPABILITIES: tuple[str, ...] = tuple(
    cap.id for cap in GOOGLE_CAPABILITIES if not cap.write
)


def _known_ids() -> str:
    return ", ".join(cap.id for cap in GOOGLE_CAPABILITIES)


def expand_capability_ids(capabilities: Iterable[str] | None) -> tuple[str, ...]:
    """Validate ids, default to read-only, and pull in the read sibling of a write."""
    if capabilities is None:
        chosen = list(DEFAULT_CONNECT_CAPABILITIES)
    else:
        chosen: list[str] = []
        seen: set[str] = set()
        for raw in capabilities:
            name = raw.strip()
            if not name or name in seen:
                continue
            if name not in GOOGLE_CAPABILITY_BY_ID:
                raise GoogleCapabilityError(
                    f"Unknown Google access {name!r}. Choose from: {_known_ids()}."
                )
            seen.add(name)
            chosen.append(name)
        if not chosen:
            raise GoogleCapabilityError("Pick at least one Google surface.")
    ordered: list[str] = []
    have = set(chosen)
    for cap in GOOGLE_CAPABILITIES:
        if cap.id in have:
            sibling = _WRITE_TO_READ.get(cap.id)
            if sibling is not None and sibling not in have:
                have.add(sibling)
                ordered.append(sibling)
            ordered.append(cap.id)
    return tuple(ordered)


def scopes_for_capabilities(capabilities: Iterable[str] | None) -> tuple[str, ...]:
    names = expand_capability_ids(capabilities)
    scopes: list[str] = list(IDENTITY_SCOPES)
    seen = set(scopes)
    for name in names:
        for scope in GOOGLE_CAPABILITY_BY_ID[name].scopes:
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return tuple(scopes)


def scope_satisfied(have: Iterable[str], needed: str) -> bool:
    granted = set(have)
    if needed in granted:
        return True
    return any(parent in granted for parent in _SCOPE_IMPLIED_BY.get(needed, ()))


def missing_scopes(have: Iterable[str], needed: Iterable[str]) -> tuple[str, ...]:
    return tuple(scope for scope in needed if not scope_satisfied(have, scope))


def capabilities_from_scopes(scopes: Iterable[str]) -> list[str]:
    """Map stored OAuth scopes back to the owner-facing boxes."""
    have = set(scopes)
    granted: list[str] = []
    for cap in GOOGLE_CAPABILITIES:
        if any(scope_satisfied(have, scope) for scope in cap.scopes):
            granted.append(cap.id)
    return granted


def capability_catalog() -> list[dict[str, str | bool]]:
    return [
        {
            "id": cap.id,
            "group": cap.group,
            "group_label": cap.group_label,
            "label": cap.label,
            "write": cap.write,
        }
        for cap in GOOGLE_CAPABILITIES
    ]
