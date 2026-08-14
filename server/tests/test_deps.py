"""api/deps.py's JWT auth dependency -- the two 401 branches inside
_decode_token (no active session; invalid/expired token) and the SSE
stream variant's query-param token path are never hit through the normal
`client`/`auth_headers` fixtures, which always present a valid header
token against an active session."""

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.datastructures import QueryParams
from starlette.requests import Request

from rivulets.api.deps import (
    SessionClaims,
    _decode_session_token,  # pyright: ignore[reportPrivateUsage]
    _decode_token,  # pyright: ignore[reportPrivateUsage]
    get_current_human_id,
    get_current_workspace_id_for_stream,
    get_session_claims,
    require_owner_grant,
)
from rivulets.security.session import get_session_key_store


def test_decode_token_raises_401_with_no_active_session() -> None:
    get_session_key_store().clear()
    with pytest.raises(HTTPException) as exc_info:
        _decode_token("anything")
    assert exc_info.value.status_code == 401
    assert "No active session" in exc_info.value.detail


def test_decode_token_raises_401_for_an_invalid_token() -> None:
    get_session_key_store().set_key(b"a-real-signing-key")
    try:
        with pytest.raises(HTTPException) as exc_info:
            _decode_token("not-a-real-jwt")
        assert exc_info.value.status_code == 401
        assert "Invalid or expired token" in exc_info.value.detail
    finally:
        get_session_key_store().clear()


async def test_get_current_workspace_id_for_stream_requires_a_token() -> None:
    request = Request({"type": "http", "query_string": b"", "headers": []})
    assert request.query_params == QueryParams("")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_workspace_id_for_stream(request, None)
    assert exc_info.value.status_code == 401


async def test_get_current_workspace_id_for_stream_accepts_a_stream_ticket_via_query_param() -> (
    None
):
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode(
            {"sub": "workspace-123", "purpose": "stream"}, signing_key, algorithm="HS256"
        )
        request = Request(
            {"type": "http", "query_string": f"token={token}".encode(), "headers": []}
        )

        claims = await get_current_workspace_id_for_stream(request, None)

        assert claims.workspace_id == "workspace-123"
    finally:
        get_session_key_store().clear()


async def test_get_current_workspace_id_for_stream_rejects_a_full_session_token() -> None:
    """A query-string token must be a purpose-scoped stream ticket (POST
    /auth/stream-ticket), not a normal session token -- even though the
    latter would decode successfully, accepting it here is exactly the
    "long-lived, powerful token leaking into logs/history" risk this
    split exists to close."""
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode(
            {"sub": "workspace-123", "grant": "owner"}, signing_key, algorithm="HS256"
        )
        request = Request(
            {"type": "http", "query_string": f"token={token}".encode(), "headers": []}
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_current_workspace_id_for_stream(request, None)
        assert exc_info.value.status_code == 401
    finally:
        get_session_key_store().clear()


async def test_get_session_claims_rejects_a_stream_ticket_used_as_a_bearer_token() -> None:
    """#234: a stream ticket (purpose="stream") is only meant to work as a
    query-string credential on get_current_workspace_id_for_stream -- if
    presented as a normal `Authorization: Bearer` header instead, it must
    not be treated as a full session, or a leaked ticket would be a
    60-second-but-otherwise-full session on every route, not just the one
    it was minted for."""
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode(
            {"sub": "workspace-123", "grant": "owner", "purpose": "stream"},
            signing_key,
            algorithm="HS256",
        )
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_session_claims(credentials)
        assert exc_info.value.status_code == 401
    finally:
        get_session_key_store().clear()


def test_decode_session_token_rejects_an_unknown_purpose() -> None:
    """Not just "stream" -- any non-null `purpose` claim marks a
    purpose-scoped ticket rather than a full session token, so a future
    ticket type is closed off by default instead of needing its own
    denylist entry here."""
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode(
            {"sub": "workspace-123", "purpose": "something-else"},
            signing_key,
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            _decode_session_token(token)
        assert exc_info.value.status_code == 401
    finally:
        get_session_key_store().clear()


def test_decode_session_token_accepts_a_normal_session_token() -> None:
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode({"sub": "workspace-123"}, signing_key, algorithm="HS256")
        claims = _decode_session_token(token)
        assert claims.workspace_id == "workspace-123"
    finally:
        get_session_key_store().clear()


async def test_get_current_workspace_id_for_stream_rejects_a_stream_ticket_via_header() -> None:
    """The header path is documented as "any normal session token, same as
    every other route" -- a stream ticket sent as a Bearer header rather
    than the `token` query param must be rejected the same way it is
    everywhere else, not accepted just because this dependency already
    knows how to read `purpose == "stream"` tokens."""
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode(
            {"sub": "workspace-123", "purpose": "stream"}, signing_key, algorithm="HS256"
        )
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        request = Request({"type": "http", "query_string": b"", "headers": []})

        with pytest.raises(HTTPException) as exc_info:
            await get_current_workspace_id_for_stream(request, credentials)
        assert exc_info.value.status_code == 401
    finally:
        get_session_key_store().clear()


def test_decode_token_defaults_grant_to_owner_for_pre_grant_tokens() -> None:
    """A token minted before the grant claim existed (#15) has no `grant`
    key in its payload at all -- must default to "owner" rather than
    raising, so nobody with a token already in flight gets locked out."""
    signing_key = b"legacy-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode({"sub": "workspace-123"}, signing_key, algorithm="HS256")
        claims = _decode_token(token)
        assert claims.grant == "owner"
        assert claims.human_id is None
    finally:
        get_session_key_store().clear()


async def test_get_current_human_id_raises_401_when_unclaimed() -> None:
    claims = SessionClaims(workspace_id="workspace-123", human_id=None, grant="owner")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_human_id(claims)
    assert exc_info.value.status_code == 401


async def test_get_current_human_id_returns_the_claimed_id() -> None:
    claims = SessionClaims(workspace_id="workspace-123", human_id="human-abc", grant="owner")
    assert await get_current_human_id(claims) == "human-abc"


async def test_require_owner_grant_allows_owner() -> None:
    claims = SessionClaims(workspace_id="workspace-123", human_id="human-abc", grant="owner")
    assert await require_owner_grant(claims) is None


async def test_require_owner_grant_rejects_invite_grant() -> None:
    claims = SessionClaims(workspace_id="workspace-123", human_id="human-abc", grant="invite")
    with pytest.raises(HTTPException) as exc_info:
        await require_owner_grant(claims)
    assert exc_info.value.status_code == 403
