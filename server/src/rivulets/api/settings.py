"""Workspace settings (key/value, JSON-encoded values — data-model.md).

Export/import as YAML (NFR-8.1) is left as a TODO: it needs to walk
agents/teams/channels/tools, not just this table.

Also synced (FR-9.1) — all keys except `ui.port`, which is excluded: it's
this node's own listen-port preference (deployment-and-networking.md
lists it as node-configurable), not shared workspace policy, and syncing
it would mean one node's local port conflict-avoidance change silently
overwriting every other node's. Every other key here (dispatcher/guard/
rivulet/sync.* behavior) is genuinely workspace-wide policy — e.g. the
loop-prevention guard thresholds should mean the same thing on every
node, not vary by which one happened to create a rivulet.
"""

import json

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.models import WorkspaceSetting
from rivulets.sync.publish import publish_current_state

router = APIRouter(prefix="/settings", tags=["settings"])

# Known keys for the `workspace_settings` table, with their defaults.
_DEFAULTS: dict[str, object] = {
    "dispatcher.model_override": None,
    "dispatcher.fallback_enabled": True,
    "model_tiers.override": None,  # {"cheap": "provider:model", "capable": "provider:model"}
    "guard.turn_limit": 10,
    "guard.cycle_window": 8,
    "guard.cycle_threshold": 3,
    "guard.timeout_minutes": 30,
    "rivulet.summarization_enabled": True,
    "rivulet.context_threshold_pct": 80,
    "rivulet.recent_messages_kept": 20,
    "sync.eager_files_lan": True,
    "sync.eager_files_wan": False,
    "ui.port": 8484,
    # #94 layer 3: workspace-wide fallback when a Workflow's own
    # on_call_agent_id is unset -- an agent id (string) or None. Not
    # validated against the agent table here, unlike Workflow.
    # on_call_agent_id's own PATCH validation -- every other key in this
    # table is an opaque JSON value already, and workflows/engine.py's
    # _maybe_notify_on_call_agent already tolerates a stale/deleted
    # agent id gracefully (skips notifying rather than erroring).
    "workflows.default_on_call_agent_id": None,
}

_NOT_SYNCED_KEYS = frozenset({"ui.port"})


class SettingsUpdate(BaseModel):
    model_config = {"extra": "allow"}


async def _current_settings(db: DbSession) -> dict[str, object]:
    result = await db.execute(select(WorkspaceSetting))
    stored = {row.key: json.loads(row.value) for row in result.scalars().all()}
    return {**_DEFAULTS, **stored}


@router.get("")
async def get_settings_values(
    db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> dict[str, object]:
    return await _current_settings(db)


@router.patch("")
async def patch_settings(
    body: SettingsUpdate, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
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
            await publish_current_state(db, "workspace_setting", key)
    return await _current_settings(db)
