"""Narrow JSON `object` values for pyright -- json.loads is `Any`/`Unknown`."""

from __future__ import annotations

from typing import cast


def as_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def as_list(value: object) -> list[object] | None:
    if isinstance(value, list):
        return cast(list[object], value)
    return None


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def string_list(value: object) -> list[str]:
    items = as_list(value)
    if items is None:
        return []
    return [item for item in items if isinstance(item, str)]
