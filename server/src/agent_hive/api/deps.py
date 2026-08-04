"""Shared FastAPI dependencies: DB session + workspace-key JWT auth."""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.db.session import get_db
from agent_hive.security.session import get_session_key_store

DbSession = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


async def get_current_workspace_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Validate the JWT issued at /auth/login and return the workspace ID
    from its `sub` claim. See docs/architecture/api-design.md#authentication-flow."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        signing_key = get_session_key_store().get_key()
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active session") from exc
    try:
        payload = jwt.decode(credentials.credentials, signing_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    return payload["sub"]


CurrentWorkspaceId = Annotated[str, Depends(get_current_workspace_id)]

__all__ = ["CurrentWorkspaceId", "DbSession", "get_current_workspace_id"]
