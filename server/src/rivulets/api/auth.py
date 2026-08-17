"""Workspace auth (api-design.md#authentication-flow, FR-1.2, FR-1.3).

NOTE: There is no dedicated install wizard yet (US-001) — until one
exists, the first successful login bootstraps the single `workspace` row
using the provided mnemonic, mirroring "generate a workspace key on first
install". Every login after that verifies against the stored bcrypt hash.
That same first-login moment also seeds the starter agent/team library
(#16, agentos/starter_content.py) — there's exactly one workspace per
install, so "first workspace creation" only ever happens once.

#247/#318: that first-login-wins scheme is fine when only the machine's
own user can reach this node, but not once it's reachable over a network
before the owner ever logs in -- there, the first valid-looking POST here
permanently claims the workspace out from under them. Gated below with
RIVULETS_REQUIRE_BOOTSTRAP_TOKEN + RIVULETS_BOOTSTRAP_TOKEN (config.py):
creating the workspace row while that flag is set requires the request to
carry a matching token, operator-set out of band (env vars), so a network
race can't win what it never received. Deliberately keyed on that flag,
not on `app_server_host == "0.0.0.0"` -- the Docker image hard-codes that
bind internally regardless of whether the host published it to loopback
(safe, no race possible) or the LAN (unsafe), so it can't tell the two
apart (see config.py's require_bootstrap_token docstring).

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

The same "can't start any earlier" constraint applies to AgentOS
registration. `sync_agents()` at app startup (app.py) cannot resolve
provider keys stored in the encrypted-SQLite fallback (Docker / no OS
keychain — security/credentials.py), because that store is unlocked by
`credential_store_key`, which only exists after this login. Agents
skipped then stay unregistered until something rebuilds the registry;
dispatch still matches them from the DB and `run_agent` then no-ops
with "not registered with AgentOS". So every successful login rebuilds
the registry *after* the session keys are set.

Rate limited per security-and-dr.md's documented "5 attempts per minute
per IP" (security/rate_limit.py) — checked before any credential work
happens, so a flood of mnemonic guesses is capped regardless of whether
any of them happen to be right.
"""

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.agentos import sync_agents
from rivulets.agentos.starter_content import seed_starter_agents, seed_starter_teams
from rivulets.api.deps import (
    DbSession,
    OptionalSessionClaims,
    OwnerGrant,
    SessionClaims,
    get_session_claims,
)
from rivulets.config import get_settings
from rivulets.db.models import Human, Workspace
from rivulets.db.session import begin_immediate
from rivulets.security import keys
from rivulets.security.rate_limit import get_login_rate_limiter
from rivulets.security.session import get_session_key_store
from rivulets.sync import get_sync_engine
from rivulets.sync.publish import drain_pending_outbound, publish_current_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_JWT_TTL = timedelta(hours=24)


class LoginRequest(BaseModel):
    key: str  # 12-word BIP-39 mnemonic
    passphrase: str | None = None
    # #247/#318: only consulted when this login is about to create the
    # workspace row *and* RIVULETS_REQUIRE_BOOTSTRAP_TOKEN is set — see
    # login()'s bootstrap-token check.
    bootstrap_token: str | None = None


class LoginResponse(BaseModel):
    token: str
    expires_at: str
    grant: str


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
        # #324: this request looks like it's about to bootstrap the
        # workspace — but a concurrent first-login (two browser tabs on a
        # fresh install) could be seeing the same `None` right now. BEGIN
        # IMMEDIATE (db/session.py) takes SQLite's write lock up front, so
        # a losing concurrent request blocks here instead of racing the
        # INSERT below; once it acquires the lock it re-checks and finds
        # the row the winner already committed, falling through to the
        # verify branch instead of trying to create a second one. (The
        # `singleton` UNIQUE constraint on Workspace, db/models.py, is the
        # backstop if this ever races anyway.)
        await begin_immediate(db)
        result = await db.execute(select(Workspace))
        workspace = result.scalar_one_or_none()

    if workspace is None:
        settings = get_settings()
        if settings.require_bootstrap_token:
            # #247/#318: this request is about to claim the single workspace
            # row over a network-reachable deployment. `settings.bootstrap_token`
            # being unset fails closed rather than being treated as "no token
            # required" -- checked before compare_digest so an unconfigured
            # node can never be claimed just by posting an empty token to
            # match an empty default.
            configured_token = settings.bootstrap_token
            supplied_token = body.bootstrap_token or ""
            if not configured_token or not hmac.compare_digest(
                supplied_token.encode(), configured_token.encode()
            ):
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "This node requires RIVULETS_BOOTSTRAP_TOKEN to initialize a workspace "
                    "(RIVULETS_REQUIRE_BOOTSTRAP_TOKEN is set)",
                )
        workspace = Workspace(key_hash=keys.hash_workspace_key(workspace_key))
        db.add(workspace)
        await db.commit()
        await db.refresh(workspace)
        # #16: seed the starter agent/team library on the one occasion a
        # workspace row is ever created (see the module docstring above —
        # there's no dedicated install wizard yet, so this doubles as it).
        await seed_starter_agents(db)
        await seed_starter_teams(db)
        # Do not sync_agents() here: session keys are not set yet, so any
        # agent whose model key lives in the credential-store fallback
        # would be skipped. The post-unlock sync below is the one that
        # actually registers them.
    elif not keys.verify_workspace_key(workspace_key, workspace.key_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect recovery phrase")

    jwt_signing_key = keys.derive_jwt_signing_key(workspace_key)
    p2p_psk = keys.derive_p2p_psk(workspace_key)
    workspace_fingerprint = keys.derive_workspace_fingerprint(workspace_key)
    credential_store_key = keys.derive_credential_store_key(workspace_key)
    webhook_secret_key = keys.derive_webhook_secret_key(workspace_key)
    session_store = get_session_key_store()
    session_store.set_key(jwt_signing_key)
    session_store.set_p2p_psk(p2p_psk)
    session_store.set_credential_store_key(credential_store_key)
    session_store.set_webhook_secret_key(webhook_secret_key)

    # Now that the credential store is unlocked, rebuild AgentOS from the
    # DB. Per-agent resolve failures are swallowed inside sync_agents
    # (NFR-2.4); a total failure here must not fail login.
    try:
        await sync_agents(db)
    except Exception:
        logger.warning("Failed to register agents after login", exc_info=True)

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
        {"sub": workspace.id, "iat": datetime.now(UTC), "exp": expires_at, "grant": "owner"},
        jwt_signing_key,
        algorithm="HS256",
    )
    return LoginResponse(token=token, expires_at=expires_at.isoformat(), grant="owner")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(claims: OptionalSessionClaims) -> None:
    """Requires a genuine *owner* session token before touching process-wide
    state (#228, #284) -- an unauthenticated POST here used to be able to
    wipe the JWT signing key, P2P PSK, credential-store key, and
    webhook-secret key, and stop sync, for every session on the node, from a
    plain cross-site form POST (#228). That was tightened to require some
    valid session, but any *valid* session -- including an invite-redeemed
    one (#15, grant="invite") -- still passed, letting an invited guest's
    Sign out button take the whole node down until the owner re-entered the
    mnemonic (#284). A missing/invalid token or a non-owner grant is a
    silent 204 no-op instead of a 401/403 so a client that's already logged
    out (or never had owner access) can still "sign out" locally without an
    error -- the UI already drops the local JWT first regardless."""
    if claims is None or claims.grant != "owner":
        return
    get_session_key_store().clear()
    await get_sync_engine().stop()


class IdentityRequest(BaseModel):
    human_id: str | None = None
    display_name: str | None = None


class IdentityResponse(BaseModel):
    token: str
    expires_at: str
    human_id: str
    display_name: str
    grant: str


@router.post("/identity", response_model=IdentityResponse)
async def claim_identity(
    body: IdentityRequest,
    db: DbSession,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
    _: OwnerGrant,
) -> IdentityResponse:
    """Claims a Human identity for the current session (#14) — a display
    claim layered on top of the existing workspace auth, not a separate
    credential (see Human's docstring, db/models.py). Owner-gated: an
    invite-redeemed session's identity is fixed at accept time (#15,
    api/invites.py) and must never be able to re-claim a different one.
    """
    if (body.human_id is None) == (body.display_name is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide exactly one of human_id (existing identity) or display_name (new one)",
        )

    if body.human_id is not None:
        human = await db.get(Human, body.human_id)
        if human is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Human not found")
    else:
        human = Human(display_name=body.display_name)
        db.add(human)
        await db.commit()
        await db.refresh(human)
        await publish_current_state(db, "human", human.id)

    jwt_signing_key = get_session_key_store().get_key()
    expires_at = datetime.now(UTC) + _JWT_TTL
    token = jwt.encode(
        {
            "sub": claims.workspace_id,
            "iat": datetime.now(UTC),
            "exp": expires_at,
            "grant": claims.grant,
            "human_id": human.id,
        },
        jwt_signing_key,
        algorithm="HS256",
    )
    return IdentityResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        human_id=human.id,
        display_name=human.display_name,
        grant=claims.grant,
    )


_STREAM_TICKET_TTL = timedelta(seconds=60)


class StreamTicketResponse(BaseModel):
    ticket: str
    expires_at: str


@router.post("/stream-ticket", response_model=StreamTicketResponse)
async def mint_stream_ticket(
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> StreamTicketResponse:
    """A short-lived, purpose-scoped token for the one route that can't use
    a normal Authorization header: GET /rivulets/{id}/stream, since the
    browser's native EventSource API can't set custom headers (api/deps.py's
    get_current_workspace_id_for_stream). The frontend calls this
    immediately before opening each stream connection and puts the ticket
    -- never the actual session token -- in that URL's query string.
    Bounding what a query-string token can do to `_STREAM_TICKET_TTL` and
    "open one SSE connection, nothing else" (the `purpose` claim rejects it
    everywhere else, including a *header*-borne use of it as a real session
    token) is what keeps a leak of it in server logs / browser history /
    Referer headers low-value, unlike the multi-hour session token that
    used to be passed the same way. No `OwnerGrant` here -- any session
    that can already open the stream itself (owner or invite-grant) can
    mint the ticket that lets it do so."""
    jwt_signing_key = get_session_key_store().get_key()
    expires_at = datetime.now(UTC) + _STREAM_TICKET_TTL
    ticket = jwt.encode(
        {
            "sub": claims.workspace_id,
            "iat": datetime.now(UTC),
            "exp": expires_at,
            "grant": claims.grant,
            "human_id": claims.human_id,
            "purpose": "stream",
        },
        jwt_signing_key,
        algorithm="HS256",
    )
    return StreamTicketResponse(ticket=ticket, expires_at=expires_at.isoformat())
