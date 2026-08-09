"""Vector-clock conflict resolution for synced entities (FR-9.6).

Each entity carries two related but distinct clocks:
  - `Entity.vector_clock` (an int column on Agent, Channel, etc.): a
    cheap, human-visible "highest version I've seen" cache — not itself
    sufficient to detect concurrent edits across nodes.
  - `VectorClockTracker(entity_type, entity_id, node_id, clock)`: the real
    per-node components of a proper vector clock. Comparing the *full set*
    of components between a local and an incoming version is what
    distinguishes "remote strictly happened after local" (safe to
    apply — FR-9.6's last-write-wins case) from "these were edited
    concurrently on two disconnected nodes" (a genuine conflict, recorded
    as a SyncConflict rather than silently overwritten, per FR-9.6's
    second sentence: conflicts must be surfaced for inspection, not
    resolved by fiat).

`apply_remote_change()` is the generic entity-sync path, driven by an
`EntitySpec` (model class + which fields are synced) — Agent, Channel,
Team, MCPServer, Rivulet, Message, and WorkspaceSetting all go through it.
Two things deliberately don't sync:

  - Foreign keys whose target entity type has no natural creation
    ordering relative to the referencing one aren't synced at all.
    `Channel.team_id` is excluded from `CHANNEL_SPEC`, and team
    membership (a join table) isn't synced either: gossipsub doesn't
    guarantee delivery order across different entity types, so a
    channel's "assigned to team X" change could arrive before team X's
    own create message has, and SQLite's foreign_keys=ON would reject
    the write. A real fix needs a retry/pending-apply queue keyed on the
    missing reference; out of scope for this pass. `Rivulet.channel_id`
    and `Message.rivulet_id`, by contrast, ARE synced despite having the
    same theoretical hazard — a rivulet/message is meaningless without
    its parent, unlike a channel's *optional* team assignment, so
    excluding them would make rivulet/message sync pointless. Instead
    `apply_remote_change`'s final commit catches IntegrityError and queues
    the message (SyncPendingInbound) rather than dropping it or crashing
    the sync-message handler — `handle_incoming_state_change` retries the
    whole queue after every subsequent successful apply, on the chance the
    missing dependency just arrived too. In practice channels and rivulets
    are created far less often than messages and the dependency chain is
    one hop, so the race window is real but narrow; this queue is what
    closes it instead of the message being silently lost forever.
  - Per-node status fields aren't synced: `MCPServer.connected` and
    `last_connected_at` reflect *this node's own* connection attempt, not
    shared state — each node reconnects to a synced MCPServer's url
    independently. `Rivulet.agentos_session_id` is the same idea: each
    node's own AgentOS instance owns its own session bookkeeping. Same
    reasoning FR-9.2 already applies to provider credentials.

`Tool` and `File` don't use the generic path at all (apply_remote_tool_change
/apply_remote_file_change, below) — FR-9.1 explicitly includes "tool code"
and "file attachments" in scope, and getting actual bytes onto a peer's
disk (not just a DB row) needs custom logic the generic field-copying
path doesn't do. For Tool, only `tool_type == "custom"` tools sync:
`mcp`-type Tool rows are a per-node cache of what a given MCPServer
offers (rediscovered independently by each node's own reconnect, matching
the MCPServer case above), and `builtin` tools aren't user-created at
all. For File, content doesn't travel in the gossipsub payload at all
(files run up to 100MB) — see file_transfer.py for the separate,
point-to-point mechanism used to actually move bytes between peers.
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.service import sync_agents
from rivulets.config import get_settings
from rivulets.db.base import Base
from rivulets.db.models import (
    Agent,
    AgentPeerPreference,
    Channel,
    File,
    Human,
    MCPServer,
    Message,
    Rivulet,
    SyncConflict,
    SyncPendingInbound,
    Team,
    Tool,
    ToolVersion,
    VectorClockTracker,
    Workflow,
    WorkflowConnection,
    WorkflowNode,
    WorkspaceSetting,
)
from rivulets.db.session import session_scope
from rivulets.sync.engine import get_sync_engine

logger = logging.getLogger(__name__)


class ClockComparison(Enum):
    LOCAL_NEWER = "local_newer"  # local strictly dominates remote -- ignore remote
    REMOTE_NEWER = "remote_newer"  # remote strictly dominates local -- apply remote
    EQUAL = "equal"  # identical -- no-op (duplicate delivery)
    CONCURRENT = "concurrent"  # neither dominates -- conflict


def compare_vector_clocks(local: dict[str, int], remote: dict[str, int]) -> ClockComparison:
    keys = local.keys() | remote.keys()
    local_ge = all(local.get(k, 0) >= remote.get(k, 0) for k in keys)
    remote_ge = all(remote.get(k, 0) >= local.get(k, 0) for k in keys)
    if local_ge and remote_ge:
        return ClockComparison.EQUAL
    if remote_ge:
        return ClockComparison.REMOTE_NEWER
    if local_ge:
        return ClockComparison.LOCAL_NEWER
    return ClockComparison.CONCURRENT


def merge_vector_clocks(local: dict[str, int], remote: dict[str, int]) -> dict[str, int]:
    return {k: max(local.get(k, 0), remote.get(k, 0)) for k in local.keys() | remote.keys()}


async def _load_vector_clock(db: AsyncSession, entity_type: str, entity_id: str) -> dict[str, int]:
    result = await db.execute(
        select(VectorClockTracker).where(
            VectorClockTracker.entity_type == entity_type,
            VectorClockTracker.entity_id == entity_id,
        )
    )
    return {row.node_id: row.clock for row in result.scalars().all()}


async def _store_vector_clock(
    db: AsyncSession, entity_type: str, entity_id: str, vector_clock: dict[str, int]
) -> None:
    for node_id, clock in vector_clock.items():
        row = await db.get(VectorClockTracker, (entity_type, entity_id, node_id))
        if row is None:
            db.add(
                VectorClockTracker(
                    entity_type=entity_type, entity_id=entity_id, node_id=node_id, clock=clock
                )
            )
        else:
            row.clock = clock


async def record_local_change(
    db: AsyncSession, entity_type: str, entity_id: str, own_node_id: str
) -> dict[str, int]:
    """Call after committing a local create/update, before publishing to
    peers. Bumps this node's own vector-clock component and returns the
    full vector clock to include in the gossipsub payload — the whole
    point of a vector clock is that every message carries the full vector,
    not just the sender's own component, so recipients can do a real
    happened-before comparison."""
    vc = await _load_vector_clock(db, entity_type, entity_id)
    vc[own_node_id] = vc.get(own_node_id, 0) + 1
    await _store_vector_clock(db, entity_type, entity_id, vc)
    await db.commit()
    return vc


@dataclass(frozen=True, slots=True)
class ApplyResult:
    applied: bool
    conflict: bool


@dataclass(frozen=True, slots=True)
class EntitySpec:
    entity_type: str
    model: type[Base]
    synced_fields: tuple[str, ...]
    # Every synced entity so far has an `id` primary key except
    # WorkspaceSetting, whose primary key is its `key` string — pk_field
    # lets it stay on this generic path instead of needing a third bespoke
    # apply function (like Tool/File) just to construct a new row.
    pk_field: str = "id"


AGENT_SPEC = EntitySpec("agent", Agent, ("name", "description", "instructions", "model"))
CHANNEL_SPEC = EntitySpec("channel", Channel, ("name", "description", "position", "archived"))
TEAM_SPEC = EntitySpec("team", Team, ("name", "description"))
MCP_SERVER_SPEC = EntitySpec("mcp_server", MCPServer, ("name", "url"))
RIVULET_SPEC = EntitySpec("rivulet", Rivulet, ("channel_id", "title", "status", "created_by"))
MESSAGE_SPEC = EntitySpec(
    "message",
    Message,
    (
        "rivulet_id",
        "sender_type",
        "sender_id",
        "sender_name",
        "content",
        "content_type",
        "metadata_json",
    ),
)
WORKSPACE_SETTING_SPEC = EntitySpec(
    "workspace_setting", WorkspaceSetting, ("value",), pk_field="key"
)
AGENT_PEER_PREFERENCE_SPEC = EntitySpec(
    "agent_peer_preference", AgentPeerPreference, ("capability_tag",), pk_field="agent_id"
)
HUMAN_SPEC = EntitySpec("human", Human, ("display_name",))
WORKFLOW_SPEC = EntitySpec("workflow", Workflow, ("name", "description"))
# workflow_id has the same FK-ordering hazard as Rivulet.channel_id/
# Message.rivulet_id (module docstring): included anyway because a node/
# connection is meaningless without its parent workflow, and the
# IntegrityError -> SyncPendingInbound retry queue closes the race window
# the same way it does for rivulets/messages.
WORKFLOW_NODE_SPEC = EntitySpec(
    "workflow_node",
    WorkflowNode,
    (
        "workflow_id",
        "name",
        "node_type",
        "agent_id",
        "config_json",
        "retry_max_attempts",
        "retry_backoff_seconds",
    ),
)
WORKFLOW_CONNECTION_SPEC = EntitySpec(
    "workflow_connection",
    WorkflowConnection,
    ("workflow_id", "from_node_id", "to_node_id", "condition_json"),
)


def _snapshot(instance: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(instance, field) for field in fields}


async def apply_remote_change(
    db: AsyncSession,
    spec: EntitySpec,
    entity_id: str,
    remote_vector_clock: dict[str, int],
    remote_node_id: str,
    payload: dict[str, Any],
) -> ApplyResult:
    """FR-9.6: apply an incoming change for any entity covered by `spec` if
    it strictly descends from what this node already knows about that
    entity; ignore it if this node is already ahead (stale/duplicate
    message); record a SyncConflict (touching no local data) if the two
    versions are concurrent."""
    local_vc = await _load_vector_clock(db, spec.entity_type, entity_id)
    comparison = compare_vector_clocks(local_vc, remote_vector_clock)

    if comparison in (ClockComparison.EQUAL, ClockComparison.LOCAL_NEWER):
        return ApplyResult(applied=False, conflict=False)

    merged = merge_vector_clocks(local_vc, remote_vector_clock)

    if comparison is ClockComparison.CONCURRENT:
        local_instance = await db.get(spec.model, entity_id)
        db.add(
            SyncConflict(
                entity_type=spec.entity_type,
                entity_id=entity_id,
                local_snapshot=json.dumps(
                    _snapshot(local_instance, spec.synced_fields) if local_instance else {}
                ),
                remote_snapshot=json.dumps(payload),
                remote_node_id=remote_node_id,
            )
        )
        # Vector-clock bookkeeping still advances even though we didn't
        # apply the payload: this node is now causally aware of both
        # versions, which is what lets a *future* incoming message be
        # correctly judged against what's already been seen.
        await _store_vector_clock(db, spec.entity_type, entity_id, merged)
        await db.commit()
        return ApplyResult(applied=False, conflict=True)

    # REMOTE_NEWER: a clean, non-conflicting update -- apply it.
    instance = await db.get(spec.model, entity_id)
    if instance is None:
        instance = spec.model(**{spec.pk_field: entity_id})
        db.add(instance)
    for field in spec.synced_fields:
        if field in payload:
            setattr(instance, field, payload[field])
    if hasattr(instance, "vector_clock"):
        setattr(instance, "vector_clock", max(merged.values()))  # noqa: B010
    try:
        # _store_vector_clock's db.get() calls can trigger an autoflush of
        # the pending `instance` insert/update above -- a bad FK (a synced
        # Rivulet.channel_id/Message.rivulet_id pointing at a parent that
        # hasn't synced here yet, see module docstring) can therefore
        # raise IntegrityError from inside this call, not just from the
        # commit() below, so both need to be inside the same try.
        await _store_vector_clock(db, spec.entity_type, entity_id, merged)
        await db.commit()
    except IntegrityError:
        # Rolling back undoes the vector-clock bump too, so a retry (or a
        # later fresh message for the same entity_id) is judged correctly
        # against pre-failure state, not silently treated as already-seen.
        await db.rollback()
        await _record_pending_inbound(
            db, spec.entity_type, entity_id, remote_vector_clock, remote_node_id, payload
        )
        logger.warning(
            "Queued %s/%s: referenced entity not found locally yet",
            spec.entity_type,
            entity_id,
        )
        return ApplyResult(applied=False, conflict=False)
    return ApplyResult(applied=True, conflict=False)


_TOOL_SYNCED_FIELDS = ("name", "description")


async def apply_remote_tool_change(
    db: AsyncSession,
    entity_id: str,
    remote_vector_clock: dict[str, int],
    remote_node_id: str,
    payload: dict[str, Any],
) -> ApplyResult:
    """Like apply_remote_change, but for `tool` specifically: beyond the
    name/description fields, the payload also carries the tool's current
    source code (FR-9.1's "tool code"). On a clean apply, writes that code
    to this node's own `tools_dir` (source_path is recomputed locally,
    never copied verbatim from the sender — the sender's tools_dir may
    live at a different filesystem path) and records a new ToolVersion so
    rollback history stays meaningful on this node too."""
    local_vc = await _load_vector_clock(db, "tool", entity_id)
    comparison = compare_vector_clocks(local_vc, remote_vector_clock)

    if comparison in (ClockComparison.EQUAL, ClockComparison.LOCAL_NEWER):
        return ApplyResult(applied=False, conflict=False)

    merged = merge_vector_clocks(local_vc, remote_vector_clock)

    if comparison is ClockComparison.CONCURRENT:
        local_tool = await db.get(Tool, entity_id)
        db.add(
            SyncConflict(
                entity_type="tool",
                entity_id=entity_id,
                local_snapshot=json.dumps(
                    _snapshot(local_tool, _TOOL_SYNCED_FIELDS) if local_tool else {}
                ),
                remote_snapshot=json.dumps({k: payload[k] for k in _TOOL_SYNCED_FIELDS}),
                remote_node_id=remote_node_id,
            )
        )
        await _store_vector_clock(db, "tool", entity_id, merged)
        await db.commit()
        return ApplyResult(applied=False, conflict=True)

    tool = await db.get(Tool, entity_id)
    source_path = str(get_settings().tools_dir / f"{payload['name']}.py")
    if tool is None:
        tool = Tool(id=entity_id, tool_type="custom", source_path=source_path)
        db.add(tool)
    for field in _TOOL_SYNCED_FIELDS:
        if field in payload:
            setattr(tool, field, payload[field])
    tool.source_path = source_path
    tool.vector_clock = max(merged.values())

    source_code = payload.get("source_code")
    if source_code is not None:
        Path(source_path).parent.mkdir(parents=True, exist_ok=True)
        Path(source_path).write_text(source_code, encoding="utf-8")
        await db.flush()
        latest_version = await db.scalar(
            select(ToolVersion.version)
            .where(ToolVersion.tool_id == tool.id)
            .order_by(ToolVersion.version.desc())
        )
        db.add(
            ToolVersion(tool_id=tool.id, version=(latest_version or 0) + 1, source_code=source_code)
        )

    await _store_vector_clock(db, "tool", entity_id, merged)
    await db.commit()
    return ApplyResult(applied=True, conflict=False)


_FILE_SYNCED_FIELDS = ("content_hash", "filename", "mime_type", "size_bytes", "message_id")


async def apply_remote_file_change(
    db: AsyncSession,
    entity_id: str,
    remote_vector_clock: dict[str, int],
    remote_node_id: str,
    payload: dict[str, Any],
) -> ApplyResult:
    """Like apply_remote_change, but for `file`: metadata syncs through the
    same LWW/conflict machinery as everything else, but content doesn't
    travel in the gossipsub payload at all — files run up to 100MB, and
    gossipsub is sized for small state-change messages, not that (see
    file_transfer.py's module docstring). Once metadata is applied, if
    this node doesn't already have a local copy of the bytes (by content
    hash), whether it fetches them from remote_node_id right away (over
    the dedicated stream protocol) or defers is governed by
    sync.eager_files_lan/_wan (issue #123) via _should_eager_fetch --
    either way remote_node_id is remembered as a known source
    (_remember_known_source) so a deferred fetch can still happen later,
    on demand (fetch_file_content_from_known_sources, used by
    api/files.py's download_file). `local_path` is always recomputed from
    this node's own files_dir, never copied verbatim from the sender,
    matching Tool's source_path handling. `File.message_id` has no FK
    constraint (see db/models.py), so unlike Rivulet/Message there's no
    ordering hazard to guard against here."""
    local_vc = await _load_vector_clock(db, "file", entity_id)
    comparison = compare_vector_clocks(local_vc, remote_vector_clock)

    if comparison in (ClockComparison.EQUAL, ClockComparison.LOCAL_NEWER):
        return ApplyResult(applied=False, conflict=False)

    merged = merge_vector_clocks(local_vc, remote_vector_clock)

    if comparison is ClockComparison.CONCURRENT:
        local_file = await db.get(File, entity_id)
        db.add(
            SyncConflict(
                entity_type="file",
                entity_id=entity_id,
                local_snapshot=json.dumps(
                    _snapshot(local_file, _FILE_SYNCED_FIELDS) if local_file else {}
                ),
                remote_snapshot=json.dumps({k: payload.get(k) for k in _FILE_SYNCED_FIELDS}),
                remote_node_id=remote_node_id,
            )
        )
        await _store_vector_clock(db, "file", entity_id, merged)
        await db.commit()
        return ApplyResult(applied=False, conflict=True)

    content_hash = payload["content_hash"]
    local_path = get_settings().files_dir / content_hash[:2] / content_hash

    file_row = await db.get(File, entity_id)
    if file_row is None:
        file_row = File(id=entity_id, local_path=str(local_path))
        db.add(file_row)
    for field in _FILE_SYNCED_FIELDS:
        if field in payload:
            setattr(file_row, field, payload[field])
    file_row.local_path = str(local_path)
    file_row.vector_clock = max(merged.values())
    await _store_vector_clock(db, "file", entity_id, merged)
    await db.commit()

    if not local_path.exists():
        await _remember_known_source(db, file_row, remote_node_id)
        if await _should_eager_fetch(db, remote_node_id):
            await _fetch_file_content(remote_node_id, content_hash, local_path)

    return ApplyResult(applied=True, conflict=False)


# WorkspaceSetting keys and defaults for issue #123's eager-sync policy --
# duplicated from api/settings.py's _DEFAULTS rather than imported, since
# api/settings.py imports sync/publish.py which imports this module; an
# import the other way would be a cycle.
_EAGER_SETTING_DEFAULTS = {"sync.eager_files_lan": True, "sync.eager_files_wan": False}


async def _should_eager_fetch(db: AsyncSession, remote_node_id: str) -> bool:
    """Whether apply_remote_file_change should fetch this file's bytes
    right now (eager) or leave it for a later on-demand fetch (lazy) --
    the branch issue #123 found missing entirely (both toggles eagerly
    fetched, unconditionally). Governed by sync.eager_files_lan/_wan,
    keyed off whether the node that told us about this file is reachable
    over the LAN or not (SyncEngine.peer_is_lan)."""
    engine = get_sync_engine()
    if not engine.running:
        return False
    key = "sync.eager_files_lan" if engine.peer_is_lan(remote_node_id) else "sync.eager_files_wan"
    row = await db.get(WorkspaceSetting, key)
    if row is None:
        return _EAGER_SETTING_DEFAULTS[key]
    return bool(json.loads(row.value))


async def _remember_known_source(db: AsyncSession, file_row: File, node_id: str) -> None:
    """Records node_id as a peer known to have this file's content, so a
    lazy (non-eager) file can still be fetched on demand later --
    api/files.py's download_file does exactly that when local_path is
    still missing at download time. Appends rather than overwrites: a
    file can accumulate multiple known sources across repeated syncs."""
    known = set(json.loads(file_row.synced_to_nodes)) if file_row.synced_to_nodes else set()
    if node_id in known:
        return
    known.add(node_id)
    file_row.synced_to_nodes = json.dumps(sorted(known))
    await db.commit()


async def _fetch_file_content(peer_id: str, content_hash: str, local_path: Path) -> None:
    engine = get_sync_engine()
    if not engine.running:
        return
    data = await engine.request_file(peer_id, content_hash)
    if data is None:
        logger.info("Peer %s doesn't have file content for hash=%s yet", peer_id, content_hash[:12])
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    logger.info("Fetched file content (%d bytes) from %s", len(data), peer_id)


async def fetch_file_content_from_known_sources(file_row: File) -> bool:
    """On-demand counterpart to the eager path above -- api/files.py's
    download_file calls this when local_path is missing (either lazy sync
    deferred the fetch, or an eager fetch failed transiently) rather than
    serving a 404 for content a peer already has. Tries every node
    recorded by _remember_known_source until one has the bytes. Returns
    whether local_path exists afterwards."""
    local_path = Path(file_row.local_path)
    if local_path.exists():
        return True
    if not file_row.synced_to_nodes:
        return False
    engine = get_sync_engine()
    if not engine.running:
        return False
    for node_id in json.loads(file_row.synced_to_nodes):
        data = await engine.request_file(node_id, file_row.content_hash)
        if data is not None:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(data)
            return True
    return False


_DISPATCH: dict[str, EntitySpec] = {
    "agent": AGENT_SPEC,
    "channel": CHANNEL_SPEC,
    "team": TEAM_SPEC,
    "mcp_server": MCP_SERVER_SPEC,
    "rivulet": RIVULET_SPEC,
    "message": MESSAGE_SPEC,
    "workspace_setting": WORKSPACE_SETTING_SPEC,
    "agent_peer_preference": AGENT_PEER_PREFERENCE_SPEC,
    "human": HUMAN_SPEC,
    "workflow": WORKFLOW_SPEC,
    "workflow_node": WORKFLOW_NODE_SPEC,
    "workflow_connection": WORKFLOW_CONNECTION_SPEC,
}

# Metadata-only views of tool/file for callers that just need "what fields
# does this entity type's conflict snapshot carry" (api/sync.py's conflict
# resolution) — NOT for the real incoming-sync path, which is why these
# aren't in _DISPATCH: applying a tool/file conflict's remote_snapshot
# this way updates name/description/hash/etc, but doesn't re-fetch source
# code or file bytes (apply_remote_tool_change/apply_remote_file_change's
# job, not conflict resolution's).
TOOL_SPEC = EntitySpec("tool", Tool, _TOOL_SYNCED_FIELDS)
FILE_SPEC = EntitySpec("file", File, _FILE_SYNCED_FIELDS)

_ALL_SPECS: dict[str, EntitySpec] = {**_DISPATCH, "tool": TOOL_SPEC, "file": FILE_SPEC}


def get_entity_spec(entity_type: str) -> EntitySpec | None:
    """Looks up the field-copying spec for any synced entity type,
    including tool/file (which use bespoke apply functions for the real
    sync path but still have a well-defined set of metadata fields)."""
    return _ALL_SPECS.get(entity_type)


async def _record_pending_inbound(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    vector_clock: dict[str, int],
    origin_node_id: str,
    payload: dict[str, Any],
) -> None:
    db.add(
        SyncPendingInbound(
            entity_type=entity_type,
            entity_id=entity_id,
            vector_clock_json=json.dumps(vector_clock),
            origin_node_id=origin_node_id,
            payload_json=json.dumps(payload),
        )
    )
    await db.commit()


async def retry_pending_inbound(db: AsyncSession) -> None:
    """Called after every successful apply (handle_incoming_state_change,
    below): the entity that just landed might be exactly what was missing
    for something queued earlier. Each row is deleted before its retry is
    attempted, not after — if it fails again (still missing, or a
    different dependency), apply_remote_change's own IntegrityError path
    re-queues it via _record_pending_inbound, so there's no window where a
    row is silently dropped because this function's own bookkeeping
    (rather than the retry itself) failed. Only entity types on the
    generic apply path can end up here — Tool/File have no FK-ordering
    hazard (see module docstring), so get_entity_spec returning None for
    an unexpected entity_type is treated as "nothing to retry", not an
    error."""
    result = await db.execute(select(SyncPendingInbound))
    pending = list(result.scalars().all())
    for row in pending:
        entity_type = row.entity_type
        entity_id = row.entity_id
        vector_clock = json.loads(row.vector_clock_json)
        origin_node_id = row.origin_node_id
        payload = json.loads(row.payload_json)
        await db.delete(row)
        await db.commit()
        spec = get_entity_spec(entity_type)
        if spec is None:
            continue
        await apply_remote_change(db, spec, entity_id, vector_clock, origin_node_id, payload)
    if pending:
        logger.info("Retried %d pending inbound sync message(s)", len(pending))


async def handle_incoming_state_change(
    entity_type: str,
    entity_id: str,
    vector_clock: dict[str, int],
    origin_node_id: str,
    payload: dict[str, Any],
) -> None:
    """SyncEngine's state-change handler (engine.py's `StateChangeHandler`
    type) — the entry point called via `asyncio.run_coroutine_threadsafe`
    from the trio thread whenever a gossipsub message arrives. Opens its
    own DB session since it isn't running inside a FastAPI request.

    Covers FR-9.1's full sync scope: agent/channel/team/mcp_server/tool/
    rivulet/message/file/workspace_setting/workflow/workflow_node/
    workflow_connection. Anything else is logged and dropped, matching
    this module's generalization path."""
    async with session_scope() as db:
        if entity_type == "tool":
            result = await apply_remote_tool_change(
                db, entity_id, vector_clock, origin_node_id, payload
            )
        elif entity_type == "file":
            result = await apply_remote_file_change(
                db, entity_id, vector_clock, origin_node_id, payload
            )
        elif entity_type in _DISPATCH:
            result = await apply_remote_change(
                db, _DISPATCH[entity_type], entity_id, vector_clock, origin_node_id, payload
            )
        else:
            logger.info("Dropping sync message for unsupported entity_type=%r", entity_type)
            return

        if result.conflict:
            logger.warning("Sync conflict recorded for %s/%s", entity_type, entity_id)
        elif result.applied:
            logger.info(
                "Applied remote change for %s/%s from %s", entity_type, entity_id, origin_node_id
            )
            if entity_type == "agent":
                # Without this, a node that only ever *receives* an Agent
                # row via sync has the DB row but no matching in-process
                # AgentOS registration -- run_agent() would raise "Agent
                # ... is not registered with AgentOS" the first time
                # anything (including issue #10's remote dispatch) tried
                # to invoke it here. Local create/update already calls
                # this itself (api/agents.py); a remotely-applied change
                # was the one path that didn't.
                await sync_agents(db)
            await retry_pending_inbound(db)
