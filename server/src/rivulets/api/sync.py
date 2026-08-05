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

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.models import SyncConflict
from rivulets.sync import get_sync_engine
from rivulets.sync.apply import get_entity_spec
from rivulets.sync.engine import PeerInfo as EnginePeerInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


class PeerOut(BaseModel):
    peer_id: str
    address: str
    connected: bool


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


class ConnectRequest(BaseModel):
    address: str


class DisconnectRequest(BaseModel):
    peer_id: str


class ResolveConflictRequest(BaseModel):
    keep: str  # "local" | "remote"


def _peer_out(peer: EnginePeerInfo) -> PeerOut:
    return PeerOut(peer_id=peer.peer_id, address=peer.address, connected=peer.connected)


@router.get("/status", response_model=SyncStatus)
async def sync_status(_: CurrentWorkspaceId) -> SyncStatus:
    engine = get_sync_engine()
    if not engine.running:
        return SyncStatus(running=False, node_id=None, peers=[], pending_changes=0)
    peers = await engine.list_peers()
    return SyncStatus(
        running=True,
        node_id=engine.node_id,
        peers=[_peer_out(p) for p in peers],
        pending_changes=0,
    )


@router.post("/connect", response_model=PeerOut)
async def sync_connect(body: ConnectRequest, _: CurrentWorkspaceId) -> PeerOut:
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
    return _peer_out(peer)


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def sync_disconnect(body: DisconnectRequest, _: CurrentWorkspaceId) -> None:
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
async def list_conflicts(db: DbSession, _: CurrentWorkspaceId) -> list[ConflictOut]:
    result = await db.execute(select(SyncConflict).where(SyncConflict.resolved.is_(False)))
    return [_conflict_out(row) for row in result.scalars().all()]


@router.post("/conflicts/{conflict_id}/resolve", response_model=ConflictOut)
async def resolve_conflict(
    conflict_id: str,
    body: ResolveConflictRequest,
    db: DbSession,
    _: CurrentWorkspaceId,
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
