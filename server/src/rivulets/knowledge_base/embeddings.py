"""Embedding generation for knowledge-base ingestion and retrieval (#98).

Calls the OpenAI embeddings API directly via the `openai` package (already
a dependency for chat) -- unlike every chat-completion call, which goes
through agno's Model abstraction (agentos/models.py's build_model),
embeddings have no agno wrapper, so this is a deliberate, narrow departure
from that pattern. v1 only supports the 'openai' provider
(text-embedding-3-small): reusing whatever `provider_config` row already
exists for 'openai' rather than adding a second, embedding-specific
configuration surface, per issue #98's own "does this need its own
config" open question, resolved conservatively for v1. A workspace with
no OpenAI provider configured can't use knowledge bases yet -- ingestion
and search both surface that as a clear, actionable error, not a silent
no-op.

Two entry points, matching the two contexts embeddings run in:
  - `embed_texts` (async): api/knowledge_bases.py's ingestion route,
    which already has an async DB session.
  - `embed_query_sync` (sync): tools/builtin/knowledge_base.py's
    retrieval tool, which -- like every other builtin tool -- runs inside
    agno's synchronous tool-call loop and reads the workspace DB via raw
    sqlite3 rather than the async engine (files.py/db_query.py's shared
    rationale).
"""

import sqlite3
from pathlib import Path

from openai import AsyncOpenAI, OpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.config import get_settings
from rivulets.db.models import ProviderConfig
from rivulets.security.credentials import get_provider_key

EMBEDDING_MODEL = "text-embedding-3-small"


class NoEmbeddingProviderError(RuntimeError):
    """No 'openai' provider_config exists to embed with (#98 v1's only
    supported embedding provider)."""


async def embed_texts(db: AsyncSession, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (ingestion path, async). Preserves input
    order -- callers zip texts/embeddings positionally."""
    config = await db.scalar(select(ProviderConfig).where(ProviderConfig.provider == "openai"))
    if config is None:
        raise NoEmbeddingProviderError(
            "Knowledge bases require an OpenAI provider configured "
            "(Settings > Providers) for embeddings."
        )
    api_key = get_provider_key(config.api_key_ref)
    client = AsyncOpenAI(api_key=api_key)
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def _resolve_openai_key_sync(db_path: Path) -> str:
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT api_key_ref FROM provider_config WHERE provider = 'openai' LIMIT 1"
        ).fetchone()
    if row is None:
        raise NoEmbeddingProviderError(
            "Knowledge bases require an OpenAI provider configured "
            "(Settings > Providers) for embeddings."
        )
    return get_provider_key(row[0])


def embed_query_sync(text: str) -> list[float]:
    """Embed a single query string (retrieval path, sync)."""
    api_key = _resolve_openai_key_sync(get_settings().db_path)
    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[text])
    return response.data[0].embedding


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Brute-force similarity for v1's Python-side ranking (see
    KnowledgeBaseChunk's docstring for why this isn't sqlite-vec)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
