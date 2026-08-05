"""Shared FastAPI dependencies: DB session + workspace-key JWT auth."""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.session import get_db
from rivulets.security.session import get_session_key_store

DbSession = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def _decode_token(token: str) -> str:
    try:
        signing_key = get_session_key_store().get_key()
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active session") from exc
    try:
        payload = jwt.decode(token, signing_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    return payload["sub"]


async def get_current_workspace_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Validate the JWT issued at /auth/login and return the workspace ID
    from its `sub` claim. See docs/architecture/api-design.md#authentication-flow."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return _decode_token(credentials.credentials)


async def get_current_workspace_id_for_stream(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Same validation as get_current_workspace_id, but also accepts the
    token via a `token` query parameter — needed only for the SSE stream
    route (api/rivulets.py), since the browser's native EventSource API
    can't set custom headers, and SSE (ADR-004) was chosen specifically
    because it works with plain EventSource. Every other route keeps
    header-only auth; a query-string token is more exposure (server logs,
    browser history) than we want to accept anywhere it isn't required.
    """
    token = credentials.credentials if credentials else request.query_params.get("token")
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return _decode_token(token)


CurrentWorkspaceId = Annotated[str, Depends(get_current_workspace_id)]
CurrentWorkspaceIdForStream = Annotated[str, Depends(get_current_workspace_id_for_stream)]

__all__ = [
    "CurrentWorkspaceId",
    "CurrentWorkspaceIdForStream",
    "DbSession",
    "get_current_workspace_id",
    "get_current_workspace_id_for_stream",
]
