"""Workspace settings (key/value, JSON-encoded values — data-model.md).

Export/import as YAML (NFR-8.1, #519) lives at GET /settings/export and
POST /settings/import: it walks agents/teams/channels/custom tools plus
this table — see workspace_yaml.py for the format, the merge-by-id-then-
name semantics, and why import refuses to resurrect sync-tombstoned
entities (#238). Node-local keys (_NOT_SYNCED_KEYS below) stay out of
the export the same way they stay out of sync, and for the same reason.

Also synced (FR-9.1) — all keys except `ui.port` and
`tools.working_directory`, which are excluded: `ui.port` is this node's
own listen-port preference (deployment-and-networking.md lists it as
node-configurable), and `tools.working_directory` is an absolute path on
this machine. Syncing either would mean one node's local preference
silently overwriting every other node's. Every other key here
(dispatcher/guard/rivulet/sync.* behavior) is genuinely workspace-wide
policy — e.g. the loop-prevention guard thresholds should mean the same
thing on every node, not vary by which one happened to create a rivulet.
"""

import json

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.models import WorkspaceSetting
from rivulets.sync.publish import publish_current_state
from rivulets.working_directory import SETTING_KEY as WORKING_DIRECTORY_KEY
from rivulets.working_directory import (
    create_directory,
    list_directories,
    normalize_working_directory,
)
from rivulets.workspace_yaml import ImportValidationError, apply_import, build_export

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
    # LAN vs WAN is decided per-peer by sync/engine.py's _is_lan_address:
    # RFC1918 / loopback / link-local / IPv6 ULA addresses are LAN; anything
    # else — including Tailscale's CGNAT range 100.64.0.0/10 and other
    # overlay/VPN prefixes — is WAN (issue #361), so cross-network meshes
    # only eager-fetch file bytes if the operator opts in here.
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
    # Workspace-wide default project folder. A channel (river) can set
    # its own, and a rivulet can override that without mutating the
    # river. None means the built-in sandbox under workspace_dir. Never
    # synced (an absolute path is meaningless on another peer).
    WORKING_DIRECTORY_KEY: None,
}

_NOT_SYNCED_KEYS = frozenset({"ui.port", WORKING_DIRECTORY_KEY})


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
        if key == WORKING_DIRECTORY_KEY:
            try:
                value = normalize_working_directory(value)
            except ValueError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
            updates[key] = value
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


class ImportSummaryOut(BaseModel):
    created: dict[str, int]
    updated: dict[str, int]
    skipped: list[str]
    warnings: list[str]


@router.get("/export", response_class=PlainTextResponse)
async def export_workspace(
    db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> PlainTextResponse:
    """The workspace definition as YAML (NFR-8.1) — agents, teams,
    channels, custom tools, and the synced settings above. Owner-only:
    the export carries agent instructions and custom tool source, both
    already owner-gated surfaces (api/tools.py's list_tool_versions)."""
    values = {
        key: value
        for key, value in (await _current_settings(db)).items()
        if key not in _NOT_SYNCED_KEYS
    }
    return PlainTextResponse(await build_export(db, values), media_type="application/yaml")


@router.post("/import", response_model=ImportSummaryOut)
async def import_workspace(
    request: Request, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> ImportSummaryOut:
    """Reconstructs the entities in a previously exported YAML document —
    body is the raw YAML text, not JSON. Validates everything before
    writing anything; a 400 lists every problem found at once."""
    try:
        text = (await request.body()).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be UTF-8 YAML") from exc
    try:
        summary = await apply_import(
            db,
            text,
            known_settings_keys=set(_DEFAULTS),
            local_only_settings_keys=_NOT_SYNCED_KEYS,
        )
    except ImportValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.errors) from exc
    return ImportSummaryOut(
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
        warnings=summary.warnings,
    )


class DirectoryEntryOut(BaseModel):
    name: str
    path: str


class DirectoryListingOut(BaseModel):
    path: str
    parent: str | None
    entries: list[DirectoryEntryOut]


class CreateDirectoryBody(BaseModel):
    parent: str
    name: str


@router.get("/directories", response_model=DirectoryListingOut)
async def get_directories(
    _: CurrentWorkspaceId,
    _o: OwnerGrant,
    path: str | None = Query(default=None),
) -> DirectoryListingOut:
    """List child folders of `path` (this user's home if omitted) so the
    Settings picker can navigate the local disk. Owner-only: invite
    sessions must not walk this machine's filesystem."""
    try:
        listing = list_directories(path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DirectoryListingOut(
        path=listing.path,
        parent=listing.parent,
        entries=[DirectoryEntryOut(name=e.name, path=e.path) for e in listing.entries],
    )


@router.post(
    "/directories", response_model=DirectoryListingOut, status_code=status.HTTP_201_CREATED
)
async def post_directory(
    body: CreateDirectoryBody, _: CurrentWorkspaceId, _o: OwnerGrant
) -> DirectoryListingOut:
    try:
        created = create_directory(body.parent, body.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    listing = list_directories(str(created.parent))
    return DirectoryListingOut(
        path=listing.path,
        parent=listing.parent,
        entries=[DirectoryEntryOut(name=e.name, path=e.path) for e in listing.entries],
    )
