"""LLM provider configuration (FR-1.4, FR-1.5, NFR-3.3).

Raw API keys never touch the database or a response body — they go
straight to the OS keychain via security/credentials.py, and only the
`provider_config.api_key_ref` reference is ever persisted or returned.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.agentos.models import AUTO_MODEL, resolve_default_provider
from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.base import uuid7
from rivulets.db.models import Agent, ProviderConfig
from rivulets.security.credentials import (
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
    # An "auto" agent's model string carries no "provider:" prefix
    # (agentos/models.py's AUTO_MODEL sentinel), so the check above doesn't
    # catch it -- it always resolves against the *effective* default
    # provider (resolve_default_provider's is_default-or-first-configured
    # fallback, same as resolve_tier_model uses), so deleting that
    # provider out from under it would leave it unrunnable instead.
    effective_default = await resolve_default_provider(db)
    if effective_default is not None and effective_default.id == provider.id:
        auto_agent = await db.scalar(select(Agent).where(Agent.model == AUTO_MODEL))
        if auto_agent is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Agent '{auto_agent.name}' is in Auto mode, which resolves against the "
                "default provider — set a different default provider before removing this one.",
            )
    delete_provider_key(provider.api_key_ref)
    await db.delete(provider)
    await db.commit()
