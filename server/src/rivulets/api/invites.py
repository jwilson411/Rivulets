"""Workspace invites (#15): a scoped, revocable way to let a second human
join without handing over the workspace mnemonic.

An invite is NOT a P2P mesh credential — accepting one never touches
libp2p/PNet/mDNS (sync/engine.py's workspace-wide pre-shared key stays
exactly what it always was: the mnemonic-derived PSK, gating full node
membership). Instead, POST /invites/accept authenticates the invited
human's browser directly against *this* node's HTTP API over plain
JWT, using a bearer secret shown to them exactly once (create_invite's
response). Deliberately lighter-weight than making the invited human's
device a durable, offline-capable peer — see db/models.py's Invite
docstring for the reasoning.

Because minting a session here reuses the currently-active
SessionKeyStore signing key (api/deps.py's SessionClaims are all signed
with that one key), accepting an invite only works while the owner's
node already has an unlocked session -- there's no other source of that
key. accept_invite surfaces that as a clear 401, not a generic auth
failure.

Owner-only: POST/GET/DELETE here (create/list/revoke) require
`grant="owner"` (api/deps.py's OwnerGrant) -- same gate applied to
providers.py, backups.py, sync.py, settings.py, update.py, and
auth.py's /auth/identity. An invite-redeemed session must never be able
to mint further invites or reach those other owner-only surfaces.
POST /invites/accept itself is the one route in this module that's
*not* owner-gated -- it's how an invite-holder becomes a session in the
first place.
"""

import logging
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.config import get_settings
from rivulets.db.models import Human, Invite, Workspace
from rivulets.security import keys
from rivulets.security.network import detect_lan_address, is_loopback_host
from rivulets.security.rate_limit import get_invite_accept_rate_limiter
from rivulets.security.session import get_session_key_store
from rivulets.sync.publish import publish_current_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invites", tags=["invites"])

_JWT_TTL = timedelta(hours=24)  # same session lifetime as a normal login (api/auth.py)
_DEFAULT_EXPIRES_IN_HOURS = 168  # 7 days


class InviteCreate(BaseModel):
    display_name_hint: str | None = None
    max_uses: int = 1
    expires_in_hours: int = _DEFAULT_EXPIRES_IN_HOURS


class InviteCreated(BaseModel):
    invite_id: str
    url: str
    expires_at: str
    # #121: `url` is only ever reachable off this machine if the owner's own
    # browser request already came in on a non-loopback host. When it
    # didn't (the common case -- NFR-3.4's loopback-only default), these
    # let the UI warn instead of silently handing out a dead link.
    loopback_only: bool
    lan_url: str | None = None


class InviteOut(BaseModel):
    id: str
    display_name_hint: str | None
    max_uses: int
    use_count: int
    expires_at: str
    revoked: bool

    model_config = {"from_attributes": True}


class InviteAccept(BaseModel):
    invite_token: str  # "<invite_id>.<secret>"
    display_name: str | None = None


class InviteAcceptResponse(BaseModel):
    token: str
    expires_at: str
    human_id: str
    display_name: str
    grant: str


@router.post("", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreate, request: Request, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> InviteCreated:
    secret = keys.generate_invite_secret()
    expires_at = datetime.now(UTC) + timedelta(hours=body.expires_in_hours)
    invite = Invite(
        secret_hash=keys.hash_invite_secret(secret),
        display_name_hint=body.display_name_hint,
        max_uses=body.max_uses,
        expires_at=expires_at.isoformat(),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    # The raw secret is returned exactly once and never persisted (only its
    # bcrypt hash is) -- same shown-once UX as the workspace mnemonic itself.
    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/invite/{invite.id}.{secret}"

    host = request.url.hostname or ""
    loopback_only = is_loopback_host(host)
    lan_url = None
    if loopback_only:
        lan_ip = detect_lan_address()
        if lan_ip and not is_loopback_host(lan_ip):
            port = request.url.port or get_settings().app_server_port
            lan_url = f"http://{lan_ip}:{port}/invite/{invite.id}.{secret}"

    return InviteCreated(
        invite_id=invite.id,
        url=url,
        expires_at=invite.expires_at,
        loopback_only=loopback_only,
        lan_url=lan_url,
    )


@router.get("", response_model=list[InviteOut])
async def list_invites(db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant) -> list[Invite]:
    result = await db.execute(select(Invite).order_by(Invite.created_at.desc()))
    return list(result.scalars().all())


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> None:
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invite not found")
    invite.revoked = True
    await db.commit()


@router.post("/accept", response_model=InviteAcceptResponse)
async def accept_invite(
    body: InviteAccept, request: Request, db: DbSession
) -> InviteAcceptResponse:
    """Deliberately not CurrentWorkspaceId/OwnerGrant-gated -- the invite
    secret itself is the credential being presented here, not a bearer
    token (the invited human doesn't have one yet)."""
    client_ip = request.client.host if request.client else "unknown"
    if not get_invite_accept_rate_limiter().check(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — try again shortly"
        )

    invite_id, _, secret = body.invite_token.partition(".")
    if not invite_id or not secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid invite link")

    invite = await db.get(Invite, invite_id)
    if invite is None or not keys.verify_invite_secret(secret, invite.secret_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid invite link")
    if invite.revoked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This invite has been revoked")
    if invite.expires_at < datetime.now(UTC).isoformat():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This invite has expired")
    if invite.use_count >= invite.max_uses:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This invite has already been used")

    result = await db.execute(select(Workspace))
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No workspace to join yet")

    try:
        jwt_signing_key = get_session_key_store().get_key()
    except RuntimeError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This workspace isn't currently unlocked on this node — ask the owner to open "
            "Rivulets here first",
        ) from exc

    human = Human(display_name=body.display_name or invite.display_name_hint or "Guest")
    db.add(human)
    invite.use_count += 1
    await db.commit()
    await db.refresh(human)
    await publish_current_state(db, "human", human.id)

    expires_at = datetime.now(UTC) + _JWT_TTL
    token = jwt.encode(
        {
            "sub": workspace.id,
            "iat": datetime.now(UTC),
            "exp": expires_at,
            "grant": "invite",
            "human_id": human.id,
        },
        jwt_signing_key,
        algorithm="HS256",
    )
    return InviteAcceptResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        human_id=human.id,
        display_name=human.display_name,
        grant="invite",
    )
