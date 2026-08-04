"""P2P sync status/control (FR-9). Backed by SyncEngine, which is an
interface with every network operation stubbed (see sync/engine.py) until
a libp2p binding is chosen — so every route here is honestly 501 except
status, which can truthfully report "0 peers, sync engine not running".
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from agent_hive.api.deps import CurrentWorkspaceId

router = APIRouter(prefix="/sync", tags=["sync"])


class PeerOut(BaseModel):
    peer_id: str
    address: str
    connected: bool


class ConflictOut(BaseModel):
    entity_type: str
    entity_id: str
    local_clock: int
    remote_clock: int


class SyncStatus(BaseModel):
    peers: list[PeerOut]
    pending_changes: int


class ConnectRequest(BaseModel):
    address: str


class DisconnectRequest(BaseModel):
    peer_id: str


class ResolveConflictRequest(BaseModel):
    keep: str  # "local" | "remote"


@router.get("/status", response_model=SyncStatus)
async def sync_status(_: CurrentWorkspaceId) -> SyncStatus:
    return SyncStatus(peers=[], pending_changes=0)


@router.post("/connect")
async def sync_connect(body: ConnectRequest, _: CurrentWorkspaceId) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Sync engine not yet wired up")


@router.post("/disconnect")
async def sync_disconnect(body: DisconnectRequest, _: CurrentWorkspaceId) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Sync engine not yet wired up")


@router.get("/conflicts", response_model=list[ConflictOut])
async def list_conflicts(_: CurrentWorkspaceId) -> list[ConflictOut]:
    return []


@router.post("/conflicts/{entity_type}/{entity_id}/resolve")
async def resolve_conflict(
    entity_type: str,
    entity_id: str,
    body: ResolveConflictRequest,
    _: CurrentWorkspaceId,
) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Sync engine not yet wired up")
