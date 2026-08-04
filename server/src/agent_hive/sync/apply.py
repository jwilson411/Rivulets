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
Team, MCPServer, Thread, and Message all go through it. Two things
deliberately don't sync:

  - Foreign keys whose target entity type has no natural creation
    ordering relative to the referencing one aren't synced at all.
    `Channel.team_id` is excluded from `CHANNEL_SPEC`, and team
    membership (a join table) isn't synced either: gossipsub doesn't
    guarantee delivery order across different entity types, so a
    channel's "assigned to team X" change could arrive before team X's
    own create message has, and SQLite's foreign_keys=ON would reject
    the write. A real fix needs a retry/pending-apply queue keyed on the
    missing reference; out of scope for this pass. `Thread.channel_id`
    and `Message.thread_id`, by contrast, ARE synced despite having the
    same theoretical hazard — a thread/message is meaningless without
    its parent, unlike a channel's *optional* team assignment, so
    excluding them would make thread/message sync pointless. Instead
    `apply_remote_change`'s final commit catches IntegrityError: if the
    parent hasn't arrived yet, the change is dropped with a warning
    rather than crashing the sync-message handler. In practice channels
    and threads are created far less often than messages and the
    dependency chain is one hop, so the race window is real but narrow;
    there's still no redelivery/retry, so a message that loses this race
    is simply never applied unless something re-publishes it.
  - Per-node status fields aren't synced: `MCPServer.connected` and
    `last_connected_at` reflect *this node's own* connection attempt, not
    shared state — each node reconnects to a synced MCPServer's url
    independently. `Thread.agentos_session_id` is the same idea: each
    node's own AgentOS instance owns its own session bookkeeping. Same
    reasoning FR-9.2 already applies to provider credentials.

`Tool` doesn't use the generic path at all (apply_remote_tool_change,
below) — FR-9.1 explicitly includes "tool code" in scope, and getting a
tool's *source code* onto a peer's disk (not just its DB row) needs
custom logic the generic field-copying path doesn't do. Only
`tool_type == "custom"` tools sync: `mcp`-type Tool rows are a per-node
cache of what a given MCPServer offers (rediscovered independently by
each node's own reconnect, matching the MCPServer case above), and
`builtin` tools aren't user-created at all.
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

from agent_hive.config import get_settings
from agent_hive.db.base import Base
from agent_hive.db.models import (
    Agent,
    Channel,
    MCPServer,
    Message,
    SyncConflict,
    Team,
    Thread,
    Tool,
    ToolVersion,
    VectorClockTracker,
)
from agent_hive.db.session import session_scope

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


AGENT_SPEC = EntitySpec("agent", Agent, ("name", "description", "instructions", "model"))
CHANNEL_SPEC = EntitySpec("channel", Channel, ("name", "description", "position", "archived"))
TEAM_SPEC = EntitySpec("team", Team, ("name", "description"))
MCP_SERVER_SPEC = EntitySpec("mcp_server", MCPServer, ("name", "url"))
THREAD_SPEC = EntitySpec("thread", Thread, ("channel_id", "title", "status", "created_by"))
MESSAGE_SPEC = EntitySpec(
    "message",
    Message,
    (
        "thread_id",
        "sender_type",
        "sender_id",
        "sender_name",
        "content",
        "content_type",
        "metadata_json",
    ),
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
        instance = spec.model(id=entity_id)
        db.add(instance)
    for field in spec.synced_fields:
        if field in payload:
            setattr(instance, field, payload[field])
    if hasattr(instance, "vector_clock"):
        setattr(instance, "vector_clock", max(merged.values()))  # noqa: B010
    try:
        # _store_vector_clock's db.get() calls can trigger an autoflush of
        # the pending `instance` insert/update above -- a bad FK (a synced
        # Thread.channel_id/Message.thread_id pointing at a parent that
        # hasn't synced here yet, see module docstring) can therefore
        # raise IntegrityError from inside this call, not just from the
        # commit() below, so both need to be inside the same try.
        await _store_vector_clock(db, spec.entity_type, entity_id, merged)
        await db.commit()
    except IntegrityError:
        # Rolling back undoes the vector-clock bump too, so a later
        # message for the same entity_id is judged fresh rather than
        # silently treated as already-seen.
        await db.rollback()
        logger.warning(
            "Dropping %s/%s: referenced entity not found locally yet",
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


_DISPATCH: dict[str, EntitySpec] = {
    "agent": AGENT_SPEC,
    "channel": CHANNEL_SPEC,
    "team": TEAM_SPEC,
    "mcp_server": MCP_SERVER_SPEC,
    "thread": THREAD_SPEC,
    "message": MESSAGE_SPEC,
}


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

    Covers FR-9.1's agent/channel/team/mcp_server/tool/thread/message sync
    scope; file attachments and workspace settings aren't wired up yet and
    are logged/dropped, matching this module's generalization path."""
    async with session_scope() as db:
        if entity_type == "tool":
            result = await apply_remote_tool_change(
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
