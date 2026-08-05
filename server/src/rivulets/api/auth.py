"""Workspace auth (api-design.md#authentication-flow, FR-1.2, FR-1.3).

NOTE: There is no dedicated install wizard yet (US-001) — until one
exists, the first successful login bootstraps the single `workspace` row
using the provided mnemonic, mirroring "generate a workspace key on first
install". Every login after that verifies against the stored bcrypt hash.

Login also starts the P2P sync engine (FR-9): the workspace PSK it needs
(FR-9.4) only exists once the workspace key has been derived here, so it
can't start any earlier (e.g. at app startup, like AgentOS does). If the
sync engine fails to start, login still succeeds — FR-9.5 says a node
must be fully functional with sync unreachable, and that includes sync
itself failing to come up, not just peers being unreachable. Once it does
start, this also drains anything queued by a previous session's failed
publishes (sync/publish.py's SyncPendingOutbound) — this is the other
half of FR-9.5's "changes sync automatically when connectivity resumes":
publish_entity_change queues on the way out, this is what actually
retries the queue.

Rate limited per security-and-dr.md's documented "5 attempts per minute
per IP" (security/rate_limit.py) — checked before any credential work
happens, so a flood of mnemonic guesses is capped regardless of whether
any of them happen to be right.
"""

import logging
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import DbSession
from rivulets.db.models import Workspace
from rivulets.security import keys
from rivulets.security.rate_limit import get_login_rate_limiter
from rivulets.security.session import get_session_key_store
from rivulets.sync import get_sync_engine
from rivulets.sync.publish import drain_pending_outbound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_JWT_TTL = timedelta(hours=24)


class LoginRequest(BaseModel):
    key: str  # 12-word BIP-39 mnemonic
    passphrase: str | None = None


class LoginResponse(BaseModel):
    token: str
    expires_at: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, db: DbSession) -> LoginResponse:
    client_ip = request.client.host if request.client else "unknown"
    if not get_login_rate_limiter().check(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts — try again shortly"
        )

    if not keys.is_valid_mnemonic(body.key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid recovery phrase")

    seed = keys.derive_seed(body.key, body.passphrase or "")
    workspace_key = keys.derive_workspace_key(seed)

    result = await db.execute(select(Workspace))
    workspace = result.scalar_one_or_none()

    if workspace is None:
        workspace = Workspace(key_hash=keys.hash_workspace_key(workspace_key))
        db.add(workspace)
        await db.commit()
        await db.refresh(workspace)
    elif not keys.verify_workspace_key(workspace_key, workspace.key_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect recovery phrase")

    jwt_signing_key = keys.derive_jwt_signing_key(workspace_key)
    p2p_psk = keys.derive_p2p_psk(workspace_key)
    workspace_fingerprint = keys.derive_workspace_fingerprint(workspace_key)
    session_store = get_session_key_store()
    session_store.set_key(jwt_signing_key)
    session_store.set_p2p_psk(p2p_psk)

    try:
        # workspace_fingerprint, not workspace.id: the DB row's id is a
        # fresh random uuid7 minted independently by whichever node
        # bootstraps it first, so two nodes on the same workspace key
        # would get different ids — the fingerprint is derived from the
        # shared key instead, so every node scopes mDNS discovery
        # identically (see keys.py's derive_workspace_fingerprint).
        await get_sync_engine().start(workspace_fingerprint.hex(), p2p_psk.hex())
        await drain_pending_outbound(db)
    except Exception:
        logger.warning("Sync engine failed to start — continuing offline", exc_info=True)

    expires_at = datetime.now(UTC) + _JWT_TTL
    token = jwt.encode(
        {"sub": workspace.id, "iat": datetime.now(UTC), "exp": expires_at},
        jwt_signing_key,
        algorithm="HS256",
    )
    return LoginResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    get_session_key_store().clear()
    await get_sync_engine().stop()
