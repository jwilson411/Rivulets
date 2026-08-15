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
POST /invites/accept and POST /invites/resume are the two routes in this
module that are *not* owner-gated -- accept is how an invite-holder
becomes a session in the first place, and resume (#350) is how that
session gets back in after a refresh or sign-out, since an invited human
has no mnemonic to re-login with and can't re-redeem a spent single-use
invite. Both present their own bearer secret instead of a session token;
resume's is the per-redemption InviteSession credential minted by accept
(see InviteSession's docstring, db/models.py).
"""

import logging
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.config import get_settings
from rivulets.db.models import Human, Invite, InviteSession, Workspace
from rivulets.security import keys
from rivulets.security.network import detect_lan_address, is_loopback_host
from rivulets.security.rate_limit import (
    get_invite_accept_rate_limiter,
    get_invite_resume_rate_limiter,
)
from rivulets.security.session import get_session_key_store
from rivulets.sync.publish import publish_current_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invites", tags=["invites"])

_JWT_TTL = timedelta(hours=24)  # same session lifetime as a normal login (api/auth.py)
_DEFAULT_EXPIRES_IN_HOURS = 168  # 7 days
# How long an invited human can stay away and still resume (#350) -- a
# sliding idle window, bumped on every successful resume, so a regularly
# returning guest never falls off while a token whose holder has moved on
# eventually dies on its own even if the owner never revokes the invite.
_RESUME_TTL = timedelta(days=30)


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
    # #350: "<invite_session_id>.<secret>", the re-entry credential for
    # POST /invites/resume. Returned by accept (freshly minted) and echoed
    # back unchanged by resume -- deliberately not rotated there, since a
    # second tab or a restored browser session resuming concurrently would
    # race a rotation and strand whichever tab held the stale value.
    resume_token: str


class InviteResume(BaseModel):
    resume_token: str  # "<invite_session_id>.<secret>"


async def _reserve_redemption_slot(db: AsyncSession, invite_id: str) -> bool:
    """Atomically claims one use of `invite_id`, returning whether the
    claim succeeded. accept_invite's own use_count >= max_uses check is
    best-effort -- a nice error message for the common sequential case --
    this compare-and-swap UPDATE is what actually keeps two concurrent
    accepts of a max_uses=1 invite from both succeeding: only one
    request's UPDATE can match a still-eligible row, so only one gets
    rowcount == 1."""
    now_iso = datetime.now(UTC).isoformat()
    result = await db.execute(
        update(Invite)
        .where(
            Invite.id == invite_id,
            Invite.revoked == False,  # noqa: E712
            Invite.use_count < Invite.max_uses,
            Invite.expires_at > now_iso,
        )
        .values(use_count=Invite.use_count + 1)
    )
    # Unlike tracing.py's prune_old_traces, an atomic rowcount really is
    # needed for correctness here (a select-then-delete/update shape would
    # reopen the exact TOCTOU race this function exists to close).
    return (  # pyright: ignore[reportUnknownVariableType]
        result.rowcount  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        == 1
    )


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

    if not await _reserve_redemption_slot(db, invite.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This invite has already been used")

    human = Human(display_name=body.display_name or invite.display_name_hint or "Guest")
    db.add(human)
    # Flush so human.id (a Python-side uuid7 default applied at INSERT)
    # exists before the InviteSession row below references it.
    await db.flush()
    # #350: the JWT below lives only in browser memory, so this per-
    # redemption credential is what lets this human back in after a
    # refresh/sign-out (POST /invites/resume) -- same shown-once,
    # hash-at-rest treatment as the invite secret itself.
    resume_secret = keys.generate_invite_secret()
    invite_session = InviteSession(
        secret_hash=keys.hash_invite_secret(resume_secret),
        invite_id=invite.id,
        human_id=human.id,
        expires_at=(datetime.now(UTC) + _RESUME_TTL).isoformat(),
    )
    db.add(invite_session)
    await db.commit()
    await db.refresh(human)
    await db.refresh(invite_session)
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
        resume_token=f"{invite_session.id}.{resume_secret}",
    )


@router.post("/resume", response_model=InviteAcceptResponse)
async def resume_invite_session(
    body: InviteResume, request: Request, db: DbSession
) -> InviteAcceptResponse:
    """Re-mints a grant="invite" session from the per-redemption credential
    accept_invite handed out (#350) -- the invited human's only way back in
    after a refresh or sign-out, since they have no mnemonic and a spent
    single-use invite can't be re-redeemed. Not session-gated for the same
    reason accept isn't: the resume secret itself is the credential.

    Status codes are part of the contract with the UI here: 401/403 mean
    "this credential is dead, discard it" (bad secret, expired window,
    revoked invite), while 429/503 mean "the credential may be fine, try
    again later" -- the browser holds the token across the latter but
    forgets it on the former. That's why the locked-node case is a 503
    here, unlike accept's 401 for the same condition.

    Deliberately NOT checked: the parent invite's own expiry and use count.
    Those gate *new* redemptions; this human already redeemed. The owner's
    lever over an already-redeemed guest is revoking the invite, which is
    checked on every resume."""
    client_ip = request.client.host if request.client else "unknown"
    if not get_invite_resume_rate_limiter().check(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — try again shortly"
        )

    session_id, _, secret = body.resume_token.partition(".")
    if not session_id or not secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid resume token")

    invite_session = await db.get(InviteSession, session_id)
    if invite_session is None or not keys.verify_invite_secret(secret, invite_session.secret_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid resume token")
    if invite_session.expires_at < datetime.now(UTC).isoformat():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This invite session has expired — ask the workspace owner for a new invite",
        )

    invite = await db.get(Invite, invite_session.invite_id)
    if invite is None or invite.revoked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This invite has been revoked")

    # Humans aren't currently deletable via the API, but a resume must
    # never resurrect an identity the DB no longer has a row for.
    human = await db.get(Human, invite_session.human_id)
    if human is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This identity no longer exists on this workspace"
        )

    result = await db.execute(select(Workspace))
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No workspace to join yet")

    try:
        jwt_signing_key = get_session_key_store().get_key()
    except RuntimeError as exc:
        # 503, not accept's 401 -- see the docstring's status-code contract.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This workspace isn't currently unlocked on this node — ask the owner to open "
            "Rivulets here first",
        ) from exc

    # Slide the idle window forward -- see _RESUME_TTL's comment.
    invite_session.expires_at = (datetime.now(UTC) + _RESUME_TTL).isoformat()
    await db.commit()

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
        resume_token=body.resume_token,
    )
