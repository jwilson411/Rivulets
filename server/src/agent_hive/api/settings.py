"""Workspace settings (key/value, JSON-encoded values — data-model.md).

Export/import as YAML (NFR-8.1) is left as a TODO: it needs to walk
agents/teams/channels/tools, not just this table.

Also synced (FR-9.1) — all keys except `ui.port`, which is excluded: it's
this node's own listen-port preference (deployment-and-networking.md
lists it as node-configurable), not shared workspace policy, and syncing
it would mean one node's local port conflict-avoidance change silently
overwriting every other node's. Every other key here (dispatcher/guard/
thread/sync.* behavior) is genuinely workspace-wide policy — e.g. the
loop-prevention guard thresholds should mean the same thing on every
node, not vary by which one happened to create a thread.
"""

import json

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_hive.api.deps import CurrentWorkspaceId, DbSession
from agent_hive.db.models import WorkspaceSetting
from agent_hive.sync.publish import publish_entity_change

router = APIRouter(prefix="/settings", tags=["settings"])

# Defaults per docs/architecture/data-model.md's `workspace_settings` known keys.
_DEFAULTS: dict[str, object] = {
    "dispatcher.model_override": None,
    "dispatcher.fallback_enabled": True,
    "guard.turn_limit": 10,
    "guard.cycle_window": 8,
    "guard.cycle_threshold": 3,
    "guard.timeout_minutes": 30,
    "thread.summarization_enabled": True,
    "thread.context_threshold_pct": 80,
    "thread.recent_messages_kept": 20,
    "sync.eager_files_lan": True,
    "sync.eager_files_wan": False,
    "ui.port": 8484,
}

_NOT_SYNCED_KEYS = frozenset({"ui.port"})


class SettingsUpdate(BaseModel):
    model_config = {"extra": "allow"}


async def _current_settings(db: DbSession) -> dict[str, object]:
    result = await db.execute(select(WorkspaceSetting))
    stored = {row.key: json.loads(row.value) for row in result.scalars().all()}
    return {**_DEFAULTS, **stored}


@router.get("")
async def get_settings_values(db: DbSession, _: CurrentWorkspaceId) -> dict[str, object]:
    return await _current_settings(db)


@router.patch("")
async def patch_settings(
    body: SettingsUpdate, db: DbSession, _: CurrentWorkspaceId
) -> dict[str, object]:
    updates = body.model_dump()
    for key, value in updates.items():
        if key not in _DEFAULTS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown setting: {key}")
        row = await db.get(WorkspaceSetting, key)
        if row is None:
            row = WorkspaceSetting(key=key, value=json.dumps(value))
            db.add(row)
        else:
            row.value = json.dumps(value)
            row.vector_clock += 1
    await db.commit()
    for key in updates:
        if key not in _NOT_SYNCED_KEYS:
            row = await db.get(WorkspaceSetting, key)
            assert row is not None
            await publish_entity_change(db, "workspace_setting", key, {"value": row.value})
    return await _current_settings(db)
