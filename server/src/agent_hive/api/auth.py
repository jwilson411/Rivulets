"""Workspace auth (api-design.md#authentication-flow, FR-1.2, FR-1.3).

NOTE: There is no dedicated install wizard yet (US-001) — until one
exists, the first successful login bootstraps the single `workspace` row
using the provided mnemonic, mirroring "generate a workspace key on first
install". Every login after that verifies against the stored bcrypt hash.
"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_hive.api.deps import DbSession
from agent_hive.db.models import Workspace
from agent_hive.security import keys
from agent_hive.security.session import get_session_key_store

router = APIRouter(prefix="/auth", tags=["auth"])

_JWT_TTL = timedelta(hours=24)


class LoginRequest(BaseModel):
    key: str  # 12-word BIP-39 mnemonic
    passphrase: str | None = None


class LoginResponse(BaseModel):
    token: str
    expires_at: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: DbSession) -> LoginResponse:
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
    get_session_key_store().set_key(jwt_signing_key)

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
