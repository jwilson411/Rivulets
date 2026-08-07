"""api/deps.py's JWT auth dependency -- the two 401 branches inside
_decode_token (no active session; invalid/expired token) and the SSE
stream variant's query-param token path are never hit through the normal
`client`/`auth_headers` fixtures, which always present a valid header
token against an active session."""

import jwt
import pytest
from fastapi import HTTPException
from starlette.datastructures import QueryParams
from starlette.requests import Request

from rivulets.api.deps import (
    _decode_token,  # pyright: ignore[reportPrivateUsage]
    get_current_workspace_id_for_stream,
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


async def test_get_current_workspace_id_for_stream_accepts_a_query_param_token() -> None:
    signing_key = b"stream-signing-key"
    get_session_key_store().set_key(signing_key)
    try:
        token = jwt.encode({"sub": "workspace-123"}, signing_key, algorithm="HS256")
        request = Request(
            {"type": "http", "query_string": f"token={token}".encode(), "headers": []}
        )

        workspace_id = await get_current_workspace_id_for_stream(request, None)

        assert workspace_id == "workspace-123"
    finally:
        get_session_key_store().clear()
