"""Knowledge base CRUD and document ingestion (#98) -- see KnowledgeBase's
docstring (db/models.py) for the full sync-scope design: definitions
sync across the workspace like Team/Agent; documents/chunks are local,
per-peer ingestion state.

Not owner-gated -- knowledge bases are agent/team-scoped content, the
same openness Team/Agent/Tool CRUD already have, not workspace policy
like budgets/providers/backups.
"""

import json

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.db.base import utcnow_iso
from rivulets.db.models import (
    Agent,
    File,
    KnowledgeBase,
    KnowledgeBaseChunk,
    KnowledgeBaseDocument,
    Team,
)
from rivulets.knowledge_base.chunking import chunk_text
from rivulets.knowledge_base.embeddings import NoEmbeddingProviderError, embed_texts
from rivulets.sync.publish import publish_current_state, publish_tombstone
from rivulets.validation import local_path_for_content_hash

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])

_MAX_INGEST_TEXT_BYTES = 2_000_000
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = ("application/json",)


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str | None = None
    scope_type: str  # 'agent' | 'team'
    agent_id: str | None = None
    team_id: str | None = None

    @model_validator(mode="after")
    def _scope_matches_ids(self) -> "KnowledgeBaseCreate":
        if self.scope_type == "agent" and (self.agent_id is None or self.team_id is not None):
            raise ValueError("scope_type='agent' requires agent_id and no team_id")
        if self.scope_type == "team" and (self.team_id is None or self.agent_id is not None):
            raise ValueError("scope_type='team' requires team_id and no agent_id")
        if self.scope_type not in ("agent", "team"):
            raise ValueError("scope_type must be 'agent' or 'team'")
        return self


class KnowledgeBaseUpdate(BaseModel):
    # scope_type/agent_id/team_id are not patchable -- same precedent
    # BudgetCapUpdate/EvalSuiteUpdate already set: re-parenting would
    # silently change what content an agent's searches draw from, so
    # delete and recreate instead.
    name: str | None = None
    description: str | None = None


class KnowledgeBaseOut(BaseModel):
    id: str
    name: str
    description: str | None
    scope_type: str
    agent_id: str | None
    team_id: str | None
    document_count: int

    model_config = {"from_attributes": True}


class KnowledgeBaseDocumentOut(BaseModel):
    id: str
    knowledge_base_id: str
    file_id: str
    status: str
    error_message: str | None
    chunk_count: int

    model_config = {"from_attributes": True}


class IngestDocumentIn(BaseModel):
    file_id: str


async def _get_kb_or_404(db: DbSession, kb_id: str) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge base not found")
    return kb


async def _document_count(db: DbSession, kb_id: str) -> int:
    result = await db.execute(
        select(KnowledgeBaseDocument).where(KnowledgeBaseDocument.knowledge_base_id == kb_id)
    )
    return len(result.scalars().all())


async def _kb_out(db: DbSession, kb: KnowledgeBase) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        scope_type=kb.scope_type,
        agent_id=kb.agent_id,
        team_id=kb.team_id,
        document_count=await _document_count(db, kb.id),
    )


@router.get("", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases(db: DbSession, _: CurrentWorkspaceId) -> list[KnowledgeBaseOut]:
    kbs = (await db.execute(select(KnowledgeBase))).scalars().all()
    return [await _kb_out(db, kb) for kb in kbs]


@router.post("", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: KnowledgeBaseCreate, db: DbSession, _: CurrentWorkspaceId
) -> KnowledgeBaseOut:
    if body.agent_id is not None and await db.get(Agent, body.agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if body.team_id is not None and await db.get(Team, body.team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")

    kb = KnowledgeBase(
        name=body.name,
        description=body.description,
        scope_type=body.scope_type,
        agent_id=body.agent_id,
        team_id=body.team_id,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    await publish_current_state(db, "knowledge_base", kb.id)
    return await _kb_out(db, kb)


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(kb_id: str, db: DbSession, _: CurrentWorkspaceId) -> KnowledgeBaseOut:
    kb = await _get_kb_or_404(db, kb_id)
    return await _kb_out(db, kb)


@router.patch("/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: str, body: KnowledgeBaseUpdate, db: DbSession, _: CurrentWorkspaceId
) -> KnowledgeBaseOut:
    kb = await _get_kb_or_404(db, kb_id)
    if body.name is not None:
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description
    kb.updated_at = utcnow_iso()
    await db.commit()
    await publish_current_state(db, "knowledge_base", kb.id)
    return await _kb_out(db, kb)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(kb_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    kb = await _get_kb_or_404(db, kb_id)
    await db.delete(kb)
    await db.commit()
    await publish_tombstone(db, "knowledge_base", kb_id)


@router.get("/{kb_id}/documents", response_model=list[KnowledgeBaseDocumentOut])
async def list_documents(
    kb_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[KnowledgeBaseDocument]:
    await _get_kb_or_404(db, kb_id)
    result = await db.execute(
        select(KnowledgeBaseDocument).where(KnowledgeBaseDocument.knowledge_base_id == kb_id)
    )
    return list(result.scalars().all())


@router.post(
    "/{kb_id}/documents",
    response_model=KnowledgeBaseDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    kb_id: str, body: IngestDocumentIn, db: DbSession, _: CurrentWorkspaceId
) -> KnowledgeBaseDocument:
    """Chunks and embeds an already-uploaded file (api/files.py's
    POST /files/upload) into this knowledge base. v1 is single-file,
    text-only, synchronous -- the request blocks until embedding
    completes (see #98's "deliberately narrow v1" note); a slow/large
    upload becoming a slow ingest call is an acceptable v1 trade-off, not
    a background job queue this app doesn't otherwise have."""
    kb = await _get_kb_or_404(db, kb_id)
    file_row = await db.get(File, body.file_id)
    if file_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    # Re-derived from files_dir + content_hash rather than trusting the
    # stored local_path column (#239/#288) -- a row whose local_path was
    # written by older/buggy code, or by a peer before the #239 apply fix,
    # would otherwise still be followed off files_dir here.
    try:
        path = local_path_for_content_hash(file_row.content_hash)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "File has an invalid content hash"
        ) from exc
    if not path.exists():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "File content hasn't synced to this node yet -- try again once it has.",
        )
    is_text = file_row.mime_type.startswith(_TEXT_MIME_PREFIXES) or (
        file_row.mime_type in _TEXT_MIME_TYPES
    )
    if not is_text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only text files can be ingested in v1 (got {file_row.mime_type!r}).",
        )
    if file_row.size_bytes > _MAX_INGEST_TEXT_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"File exceeds the {_MAX_INGEST_TEXT_BYTES}-byte ingestion limit for v1.",
        )

    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_text(text)

    document = KnowledgeBaseDocument(knowledge_base_id=kb.id, file_id=file_row.id)
    db.add(document)
    await db.flush()  # assigns document.id (uuid7 default) before chunks reference it

    if not chunks:
        document.status = "ingested"
        document.chunk_count = 0
        await db.commit()
        await db.refresh(document)
        return document

    try:
        vectors = await embed_texts(db, chunks)
    except NoEmbeddingProviderError as exc:
        document.status = "failed"
        document.error_message = str(exc)
        await db.commit()
        await db.refresh(document)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    for index, (chunk_content, vector) in enumerate(zip(chunks, vectors, strict=True)):
        db.add(
            KnowledgeBaseChunk(
                knowledge_base_id=kb.id,
                document_id=document.id,
                chunk_index=index,
                content=chunk_content,
                embedding_json=json.dumps(vector),
            )
        )
    document.status = "ingested"
    document.chunk_count = len(chunks)
    await db.commit()
    await db.refresh(document)
    return document


@router.delete("/{kb_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: str, document_id: str, db: DbSession, _: CurrentWorkspaceId
) -> None:
    await _get_kb_or_404(db, kb_id)
    document = await db.get(KnowledgeBaseDocument, document_id)
    if document is None or document.knowledge_base_id != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await db.delete(document)
    await db.commit()
