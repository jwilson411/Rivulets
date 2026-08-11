"""P2P sync status/control (FR-9). Backed by the real SyncEngine
(sync/engine.py) — connect/disconnect drive real libp2p connections,
conflicts are real SyncConflict rows detected by sync/apply.py's
vector-clock comparison during gossipsub message handling.

`pending_changes` on /status is honestly always 0 right now: there's no
offline outbox yet (FR-9.5's "changes sync automatically when
connectivity resumes" implies queuing something to resend) — publishing
while the engine isn't running just logs and drops
(engine.py:publish_state_change), it doesn't queue. That's a real gap in
FR-9.5's coverage, not a stub answer for something otherwise built.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.config import get_settings
from rivulets.db.models import SyncConflict
from rivulets.sync import get_sync_engine
from rivulets.sync.apply import get_entity_spec
from rivulets.sync.capabilities import load_capabilities, save_capabilities
from rivulets.sync.engine import PeerInfo as EnginePeerInfo

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


@router.get("/status", response_model=SyncStatus)
async def sync_status(_: CurrentWorkspaceId, _o: OwnerGrant) -> SyncStatus:
    engine = get_sync_engine()
    if not engine.running:
        return SyncStatus(running=False, node_id=None, peers=[], pending_changes=0)
    peers = await engine.list_peers()
    peer_capabilities = await engine.list_peer_capabilities()
    return SyncStatus(
        running=True,
        node_id=engine.node_id,
        peers=[_peer_out(p, peer_capabilities.get(p.peer_id, [])) for p in peers],
        pending_changes=0,
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

    if body.keep == "remote":
        spec = get_entity_spec(conflict.entity_type)
        if spec is not None:
            instance = await db.get(spec.model, conflict.entity_id)
            if instance is not None:
                remote = json.loads(conflict.remote_snapshot)
                for field in spec.synced_fields:
                    if field in remote:
                        setattr(instance, field, remote[field])

    conflict.resolved = True
    await db.commit()
    await db.refresh(conflict)
    return _conflict_out(conflict)
