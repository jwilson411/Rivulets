"""P2P sync status/control (FR-9). Backed by the real SyncEngine
(sync/engine.py) — connect/disconnect drive real libp2p connections,
conflicts are real SyncConflict rows detected by sync/apply.py's
vector-clock comparison during gossipsub message handling.

`pending_changes` on /status is the real backlog (#347): outbound
entities queued because a publish couldn't be made or failed
(SyncPendingOutbound, drained on engine start / peer connect) plus
inbound messages queued on an FK-ordering miss (SyncPendingInbound,
retried after every successful apply). Reported whether or not the
engine is running — a backlog accumulated while logged out is exactly
when the number matters.
"""

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from rivulets.agentos import sync_agents
from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.config import get_settings
from rivulets.db.models import File, SyncConflict, SyncPendingInbound, SyncPendingOutbound, Tool
from rivulets.sync import get_sync_engine
from rivulets.sync.apply import (
    AGENTOS_RESYNC_ENTITY_TYPES,
    clear_delete_blockers,
    current_vector_clock,
    ensure_file_content_available,
    entity_pk_value,
    get_entity_spec,
    new_entity_instance,
    record_resolution,
    resolution_stamp,
    write_tool_source,
)
from rivulets.sync.capabilities import load_capabilities, save_capabilities
from rivulets.sync.engine import PeerInfo as EnginePeerInfo
from rivulets.sync.publish import build_entity_payload, publish_current_state, publish_tombstone
from rivulets.validation import TOOL_NAME_RE, local_path_for_content_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


class PeerOut(BaseModel):
    peer_id: str
    address: str
    connected: bool
    capabilities: list[str] = []


class ConflictOut(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    # Snapshot values are whatever JSON a given entity's synced_fields
    # happen to contain — dict[str, str] only ever worked by coincidence,
    # back when 'agent' (all-string fields) was the only entity type that
    # could conflict. Channel/Rivulet/MCPServer/etc. fields include
    # int/bool/None too.
    local_snapshot: dict[str, Any]
    remote_snapshot: dict[str, Any]
    remote_node_id: str
    detected_at: str


class SyncStatus(BaseModel):
    running: bool
    node_id: str | None
    peers: list[PeerOut]
    pending_changes: int
    own_addresses: list[str] = []


class ConnectRequest(BaseModel):
    address: str


class DisconnectRequest(BaseModel):
    peer_id: str


class ResolveConflictRequest(BaseModel):
    keep: str  # "local" | "remote"


class CapabilitiesOut(BaseModel):
    capabilities: list[str]


class CoordinatorOut(BaseModel):
    running: bool
    node_id: str | None
    coordinator_id: str | None
    term: int
    is_self: bool
    self_score: float
    peer_scores: dict[str, float]


class SetCapabilitiesRequest(BaseModel):
    capabilities: list[str]


def _peer_out(peer: EnginePeerInfo, capabilities: list[str]) -> PeerOut:
    return PeerOut(
        peer_id=peer.peer_id,
        address=peer.address,
        connected=peer.connected,
        capabilities=capabilities,
    )


async def _count_pending_changes(db: DbSession) -> int:
    outbound = await db.scalar(select(func.count()).select_from(SyncPendingOutbound))
    inbound = await db.scalar(select(func.count()).select_from(SyncPendingInbound))
    return (outbound or 0) + (inbound or 0)


@router.get("/status", response_model=SyncStatus)
async def sync_status(db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant) -> SyncStatus:
    engine = get_sync_engine()
    pending_changes = await _count_pending_changes(db)
    if not engine.running:
        return SyncStatus(running=False, node_id=None, peers=[], pending_changes=pending_changes)
    peers = await engine.list_peers()
    peer_capabilities = await engine.list_peer_capabilities()
    return SyncStatus(
        running=True,
        node_id=engine.node_id,
        peers=[_peer_out(p, peer_capabilities.get(p.peer_id, [])) for p in peers],
        pending_changes=pending_changes,
        own_addresses=engine.own_addresses,
    )


@router.get("/capabilities", response_model=CapabilitiesOut)
async def get_capabilities(_: CurrentWorkspaceId, _o: OwnerGrant) -> CapabilitiesOut:
    return CapabilitiesOut(capabilities=load_capabilities(get_settings().sync_dir))


@router.patch("/capabilities", response_model=CapabilitiesOut)
async def set_capabilities(
    body: SetCapabilitiesRequest, _: CurrentWorkspaceId, _o: OwnerGrant
) -> CapabilitiesOut:
    save_capabilities(get_settings().sync_dir, body.capabilities)
    engine = get_sync_engine()
    if engine.running:
        await engine.publish_capabilities(body.capabilities)
    return CapabilitiesOut(capabilities=body.capabilities)


@router.get("/coordinator", response_model=CoordinatorOut)
async def get_coordinator(_: CurrentWorkspaceId, _o: OwnerGrant) -> CoordinatorOut:
    """#101: current bully-election coordinator status for
    workspace-singleton work. Mirrors sync_status's "not running" shape
    (benign default, not an error) for the same reason -- a single-peer
    or not-yet-logged-in workspace isn't a failure state."""
    engine = get_sync_engine()
    if not engine.running:
        return CoordinatorOut(
            running=False,
            node_id=None,
            coordinator_id=None,
            term=0,
            is_self=False,
            self_score=0.0,
            peer_scores={},
        )
    coord = await engine.get_coordinator_status()
    return CoordinatorOut(
        running=True,
        node_id=engine.node_id,
        coordinator_id=coord.coordinator_id,
        term=coord.term,
        is_self=coord.is_self,
        self_score=coord.self_score,
        peer_scores=coord.peer_scores,
    )


@router.post("/coordinator/reclaim", response_model=CoordinatorOut)
async def reclaim_coordinator(_: CurrentWorkspaceId, _o: OwnerGrant) -> CoordinatorOut:
    """Human-triggered failback override -- election never auto-fails-back
    on its own (see engine.py's _coordinator_tick), by design: a
    coordinator that flaps back and forth as a higher-spec peer blinks on
    and off is worse than a stable-but-suboptimal one. This is the
    explicit "I want it back now" action instead."""
    engine = get_sync_engine()
    if not engine.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sync engine is not running")
    await engine.reclaim_coordinator()
    coord = await engine.get_coordinator_status()
    return CoordinatorOut(
        running=True,
        node_id=engine.node_id,
        coordinator_id=coord.coordinator_id,
        term=coord.term,
        is_self=coord.is_self,
        self_score=coord.self_score,
        peer_scores=coord.peer_scores,
    )


@router.post("/connect", response_model=PeerOut)
async def sync_connect(body: ConnectRequest, _: CurrentWorkspaceId, _o: OwnerGrant) -> PeerOut:
    engine = get_sync_engine()
    if not engine.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sync engine is not running")
    try:
        peer = await engine.connect(body.address)
    except Exception as exc:
        logger.warning("Manual connect to %s failed", body.address, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not connect to {body.address}: {exc}"
        ) from exc
    return _peer_out(peer, [])


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def sync_disconnect(body: DisconnectRequest, _: CurrentWorkspaceId, _o: OwnerGrant) -> None:
    engine = get_sync_engine()
    if not engine.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sync engine is not running")
    await engine.disconnect(body.peer_id)


def _conflict_out(row: SyncConflict) -> ConflictOut:
    return ConflictOut(
        id=row.id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        local_snapshot=json.loads(row.local_snapshot),
        remote_snapshot=json.loads(row.remote_snapshot),
        remote_node_id=row.remote_node_id,
        detected_at=row.detected_at,
    )


@router.get("/conflicts", response_model=list[ConflictOut])
async def list_conflicts(db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant) -> list[ConflictOut]:
    result = await db.execute(select(SyncConflict).where(SyncConflict.resolved.is_(False)))
    return [_conflict_out(row) for row in result.scalars().all()]


@router.post("/conflicts/{conflict_id}/resolve", response_model=ConflictOut)
async def resolve_conflict(
    conflict_id: str,
    body: ResolveConflictRequest,
    db: DbSession,
    _: CurrentWorkspaceId,
    _o: OwnerGrant,
) -> ConflictOut:
    conflict = await db.get(SyncConflict, conflict_id)
    if conflict is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conflict not found")
    if body.keep not in ("local", "remote"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "keep must be 'local' or 'remote'")

    source_path_to_unlink: str | None = None
    if body.keep == "remote":
        spec = get_entity_spec(conflict.entity_type)
        if spec is not None:
            instance = await db.get(spec.model, entity_pk_value(spec, conflict.entity_id))
            remote = json.loads(conflict.remote_snapshot)
            if remote.get("deleted"):
                # #238: a modify/delete conflict's remote_snapshot is
                # {"deleted": True}, not a set of spec.synced_fields --
                # without this branch the loop below would find none of
                # its fields in `remote` and silently do nothing,
                # making "keep remote" on a delete-conflict a no-op
                # that looks like it worked.
                if instance is not None:
                    if isinstance(instance, Tool) and instance.tool_type == "custom":
                        # #362, same as apply_remote_delete: the source
                        # file is this node's own artifact and would
                        # otherwise outlive the kept delete.
                        source_path_to_unlink = instance.source_path
                    await clear_delete_blockers(db, conflict.entity_type, conflict.entity_id)
                    await db.delete(instance)
            else:
                if conflict.entity_type == "tool" and not TOOL_NAME_RE.match(
                    remote.get("name", "")
                ):
                    # Same reasoning as apply_remote_tool_change's #239
                    # check: the name is exec'd against as a Python
                    # identifier, and conflict snapshots are recorded
                    # *before* that check ever ran on the payload.
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Remote snapshot has an invalid tool name",
                    )
                if instance is None:
                    # #325: the local row can be gone -- e.g. this node
                    # deleted it locally while a peer concurrently edited
                    # it, so apply_remote_change's own CONCURRENT branch
                    # recorded the conflict with no local instance to work
                    # with. "Keep remote" must recreate the row (same shape
                    # apply_remote_change uses for a brand-new entity_id),
                    # not silently no-op because there was nothing to
                    # `setattr` onto.
                    instance = new_entity_instance(spec, conflict.entity_id)
                    if isinstance(instance, Tool):
                        # Bespoke-path columns the generic spec doesn't
                        # cover -- only custom tools ever sync/conflict,
                        # and source_path is always this node's own
                        # tools_dir (see apply_remote_tool_change).
                        instance.tool_type = "custom"
                        instance.source_path = str(
                            get_settings().tools_dir / f"{conflict.entity_id}.py"
                        )
                    db.add(instance)
                for field in spec.synced_fields:
                    if field in remote:
                        setattr(instance, field, remote[field])
                if isinstance(instance, Tool) and remote.get("source_code") is not None:
                    # #348: the chosen side's *bytes*, not just its
                    # metadata -- without this, "keep remote" renamed the
                    # tool while this node kept executing its own
                    # pre-conflict source under that name.
                    instance.source_path = str(
                        get_settings().tools_dir / f"{conflict.entity_id}.py"
                    )
                    await write_tool_source(db, instance, remote["source_code"])
                if isinstance(instance, File) and remote.get("content_hash"):
                    # #348: local_path must follow the chosen content_hash
                    # (apply_remote_file_change always derives it the same
                    # way) or the row keeps pointing at the old content's
                    # path on disk.
                    try:
                        instance.local_path = str(
                            local_path_for_content_hash(remote["content_hash"])
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            "Remote snapshot has an invalid content_hash",
                        ) from exc
                if hasattr(instance, "vector_clock"):
                    vc = await current_vector_clock(db, conflict.entity_type, conflict.entity_id)
                    if vc:
                        setattr(instance, "vector_clock", max(vc.values()))  # noqa: B010

    # #348: stamp this resolution in the SyncResolution register before
    # publishing it under the same stamp -- if another node resolved the
    # same conflict independently, whichever (resolved_at, node_id) pair is
    # later wins on every node (apply.py's _resolution_supersedes), instead
    # of the two resolution publishes swapping states or re-conflicting.
    engine = get_sync_engine()
    resolved_at = resolution_stamp()
    conflict.resolved = True
    try:
        await record_resolution(
            db,
            conflict.entity_type,
            conflict.entity_id,
            resolved_at,
            engine.node_id if engine.running else "",
        )
        await db.commit()
    except IntegrityError as exc:
        # A kept-remote tool name can collide with a different local custom
        # tool's (idx_tool_custom_name) -- surface it rather than 500.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Remote snapshot collides with existing local data",
        ) from exc
    await db.refresh(conflict)
    if source_path_to_unlink:
        Path(source_path_to_unlink).unlink(missing_ok=True)

    if body.keep == "remote" and conflict.entity_type in AGENTOS_RESYNC_ENTITY_TYPES:
        # #390 (#362 leftover): the keep-remote apply above is the same
        # class of change as an incoming sync apply -- a tool source
        # write/delete (or an agent/tool-assignment change) is resolved at
        # agent *build* time, so already-registered agents keep executing
        # the pre-conflict in-memory state until the registry is rebuilt.
        # "keep local" changed nothing AgentOS could see, so it skips this.
        await sync_agents(db)

    if body.keep == "remote" and conflict.entity_type == "file":
        file_row = await db.get(File, conflict.entity_id)
        if file_row is not None:
            # #348: the chosen hash may name bytes this node never fetched
            # -- record the conflicting peer as a known source (and
            # eager-fetch per the #123 policy) so they're actually
            # obtainable, same as a clean incoming file sync.
            await ensure_file_content_available(db, file_row, conflict.remote_node_id)

    # #325/#348: a resolved conflict must actually converge the mesh, not
    # just silence this node's own copy of it. Both sides already merged to
    # the same vector clock the moment the conflict was detected
    # (apply_remote_change/apply_remote_delete's CONCURRENT branch), so
    # without republishing here, this node's data can never be judged newer
    # than what peers already have -- the mesh stays split even though the
    # UI reports the conflict resolved. That holds for *both* keeps: after
    # the "keep remote" apply above, local state IS the chosen snapshot, so
    # publishing current state publishes the choice either way.
    # publish_current_state/publish_tombstone bump this node's own clock
    # component past the merged value (via record_local_change), so peers
    # apply it as REMOTE_NEWER; the resolution stamp settles the case where
    # a peer resolved concurrently. Whether the local row still exists now
    # decides which one publishes: a kept local delete and a kept remote
    # delete both mean publishing the tombstone.
    payload = await build_entity_payload(db, conflict.entity_type, conflict.entity_id)
    if payload is not None:
        await publish_current_state(
            db, conflict.entity_type, conflict.entity_id, resolution=resolved_at
        )
    else:
        await publish_tombstone(
            db, conflict.entity_type, conflict.entity_id, resolution=resolved_at
        )

    return _conflict_out(conflict)
