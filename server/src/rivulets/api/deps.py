"""Shared FastAPI dependencies: DB session + workspace-key JWT auth."""

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.session import get_db
from rivulets.security.session import get_session_key_store

DbSession = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class SessionClaims:
    """A decoded session JWT's payload (#14/#15). `human_id` is None until
    a browser session claims an identity via POST /auth/identity — it's a
    lightweight claim, not a separate credential (see Human's docstring in
    db/models.py). `grant` distinguishes an owner session (mnemonic-
    authenticated at /auth/login) from an invite-redeemed one (#15,
    /invites/accept) — it's server-decided at mint time, never
    client-supplied, and is what makes an invited session genuinely scoped
    rather than just differently labeled. Tokens minted before this claim
    existed have no `grant` in their payload; defaulting to "owner" keeps
    them valid rather than locking out anyone with a token in flight."""

    workspace_id: str
    human_id: str | None
    grant: str
    # None for every normal session token (login/identity/invite-accept).
    # "stream" marks a short-lived ticket minted by POST /auth/stream-ticket
    # (api/auth.py) -- see get_current_workspace_id_for_stream below for
    # why that distinction exists at all.
    purpose: str | None = None


def _decode_token(token: str) -> SessionClaims:
    try:
        signing_key = get_session_key_store().get_key()
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active session") from exc
    try:
        payload = jwt.decode(token, signing_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    return SessionClaims(
        workspace_id=payload["sub"],
        human_id=payload.get("human_id"),
        grant=payload.get("grant", "owner"),
        purpose=payload.get("purpose"),
    )


def _decode_session_token(token: str) -> SessionClaims:
    """Like _decode_token, but rejects any purpose-scoped ticket (#234) --
    a token minted by POST /auth/stream-ticket carries `purpose == "stream"`
    and decodes just fine (same signing key), but it's only meant to work
    as a query-string credential on the one SSE route that needs it
    (get_current_workspace_id_for_stream below). Every route that reaches
    a session via the Authorization header -- which is every route except
    that one -- must go through here instead of the bare _decode_token, or
    a 60-second stream ticket leaked into logs/history/Referer would be a
    full session token anywhere it's replayed as a Bearer header."""
    claims = _decode_token(token)
    if claims.purpose is not None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A purpose-scoped ticket cannot be used as a session token",
        )
    return claims


async def get_session_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> SessionClaims:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return _decode_session_token(credentials.credentials)


async def get_optional_session_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> SessionClaims | None:
    """Like get_session_claims, but returns None instead of 401ing on a
    missing or invalid token -- for the handful of routes (POST
    /auth/logout) where an unauthenticated caller should get a silent
    no-op instead of an error, so a client that's already logged out (or
    never was) can still call it, but nothing about the session actually
    changes unless the token is genuine."""
    if credentials is None:
        return None
    try:
        return _decode_session_token(credentials.credentials)
    except HTTPException:
        return None


async def get_current_workspace_id(
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> str:
    """Validate the JWT issued at /auth/login and return the workspace ID
    from its `sub` claim."""
    return claims.workspace_id


async def get_current_workspace_id_for_stream(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> SessionClaims:
    """Same validation as get_current_workspace_id, but also accepts a
    token via a `token` query parameter — needed only for the SSE stream
    route (api/rivulets.py), since the browser's native EventSource API
    can't set custom headers, and SSE (ADR-004) was chosen specifically
    because it works with plain EventSource. Every other route keeps
    header-only auth; a query-string token is more exposure (server logs,
    browser history, Referer headers on any cross-origin sub-resource load
    from that page) than we want to accept anywhere it isn't required.

    A token arriving via the header is any normal session token, same as
    every other route. A token arriving via the query param must instead
    be a short-lived, purpose-scoped ticket from POST /auth/stream-ticket
    (`purpose == "stream"`, api/auth.py's mint_stream_ticket) — a full,
    hours-long session token is rejected there even though it would
    decode successfully, so the only thing that can ever end up in a URL
    is something that expires in seconds and is useless anywhere else.

    Returns the full SessionClaims, not just the workspace id, because the
    stream route (streaming.py's subscribe/publish) needs `grant` too --
    #286: owner-only payloads like a fresh invite secret must never reach
    an invite-grant session's EventSource, and the stream ticket carries
    the original session's grant along (api/auth.py's mint_stream_ticket)
    specifically so this check still works for a query-string ticket."""
    if credentials is not None:
        return _decode_session_token(credentials.credentials)
    token = request.query_params.get("token")
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    claims = _decode_token(token)
    if claims.purpose != "stream":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A query-string token must be a short-lived stream ticket "
            "(POST /auth/stream-ticket), not a full session token",
        )
    return claims


async def get_current_human_id(
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> str:
    """The claimed Human for this session (#14). 401s rather than falling
    back to some default identity — every message needs a real author, and
    "no identity claimed yet" is a distinct state the UI handles via
    IdentityPicker, not something to paper over here."""
    if claims.human_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No identity claimed for this session")
    return claims.human_id


async def require_owner_grant(
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> None:
    """Gates routes an invited human (#15, grant="invite") shouldn't reach
    — provider credentials, backups, sync config, invite management itself,
    etc. See api/invites.py's module docstring for the full list of what's
    owner-only vs. open to any grant."""
    if claims.grant != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner access required")


CurrentWorkspaceId = Annotated[str, Depends(get_current_workspace_id)]
CurrentStreamClaims = Annotated[SessionClaims, Depends(get_current_workspace_id_for_stream)]
CurrentHumanId = Annotated[str, Depends(get_current_human_id)]
OwnerGrant = Annotated[None, Depends(require_owner_grant)]
OptionalSessionClaims = Annotated[SessionClaims | None, Depends(get_optional_session_claims)]

__all__ = [
    "CurrentHumanId",
    "CurrentStreamClaims",
    "CurrentWorkspaceId",
    "DbSession",
    "OptionalSessionClaims",
    "OwnerGrant",
    "SessionClaims",
    "get_current_human_id",
    "get_current_workspace_id",
    "get_current_workspace_id_for_stream",
    "get_optional_session_claims",
    "get_session_claims",
    "require_owner_grant",
]
