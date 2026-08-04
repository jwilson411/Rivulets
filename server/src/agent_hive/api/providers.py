"""LLM provider configuration (FR-1.4, FR-1.5, NFR-3.3).

Raw API keys never touch the database or a response body — they go
straight to the OS keychain via security/credentials.py, and only the
`provider_config.api_key_ref` reference is ever persisted or returned.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_hive.api.deps import CurrentWorkspaceId, DbSession
from agent_hive.db.base import uuid7
from agent_hive.db.models import Agent, ProviderConfig
from agent_hive.security.credentials import (
    CredentialStoreError,
    delete_provider_key,
    store_provider_key,
)

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderCreate(BaseModel):
    provider: str  # 'openai' | 'anthropic' | 'deepseek' | 'openai_compatible'
    label: str
    api_key: str
    base_url: str | None = None


class ProviderUpdate(BaseModel):
    label: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class ProviderOut(BaseModel):
    id: str
    provider: str
    label: str
    base_url: str | None
    is_default: bool

    model_config = {"from_attributes": True}


async def _get_or_404(db: DbSession, provider_id: str) -> ProviderConfig:
    provider = await db.get(ProviderConfig, provider_id)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    return provider


@router.get("", response_model=list[ProviderOut])
async def list_providers(db: DbSession, _: CurrentWorkspaceId) -> list[ProviderConfig]:
    result = await db.execute(select(ProviderConfig))
    return list(result.scalars().all())


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def add_provider(
    body: ProviderCreate, db: DbSession, _: CurrentWorkspaceId
) -> ProviderConfig:
    provider_id = uuid7()
    try:
        api_key_ref = store_provider_key(provider_id, body.api_key)
    except CredentialStoreError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    provider = ProviderConfig(
        id=provider_id,
        provider=body.provider,
        label=body.label,
        api_key_ref=api_key_ref,
        base_url=body.base_url,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str, body: ProviderUpdate, db: DbSession, _: CurrentWorkspaceId
) -> ProviderConfig:
    provider = await _get_or_404(db, provider_id)
    if body.label is not None:
        provider.label = body.label
    if body.base_url is not None:
        provider.base_url = body.base_url
    if body.api_key is not None:
        try:
            store_provider_key(provider_id, body.api_key)
        except CredentialStoreError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    await db.commit()
    await db.refresh(provider)
    return provider


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_provider(provider_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    provider = await _get_or_404(db, provider_id)
    in_use = await db.scalar(select(Agent).where(Agent.model.startswith(f"{provider.provider}:")))
    if in_use is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Agent '{in_use.name}' uses this provider — reassign it before removing.",
        )
    delete_provider_key(provider.api_key_ref)
    await db.delete(provider)
    await db.commit()
