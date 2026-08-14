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

`record_embedding_run` (#320): ingestion never went through
agentos/accounting.py's `record_agent_run` at all before this -- an
embedding call has no `Agent` and no agno `RunOutput`, unlike every other
billed call in this codebase, so its spend was invisible to the usage
dashboard and to budget caps. Reuses `record_agent_run` anyway (rather
than a bespoke AgentRun(...) construction) via a duck-typed stand-in for
`RunOutput` -- `agent_id=None`, same "can't attribute to a single agent"
precedent #246's dispatcher_call rows already established (see
AgentRun.agent_id's docstring) -- so ingestion spend counts toward
workspace-scope budget caps the same way every other unattributable spend
already does. Retrieval (`embed_query_sync`) is not accounted for here:
it's a per-query, per-tool-call cost on an already-billed agent turn
(#98/#320's scope stayed at ingestion, the actually-unbounded spend path
the issue called out).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from openai import AsyncOpenAI, OpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.config import get_settings
from rivulets.db.models import AgentRun, ProviderConfig
from rivulets.security.credentials import get_provider_key

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_PROVIDER_MODEL = f"openai:{EMBEDDING_MODEL}"


class NoEmbeddingProviderError(RuntimeError):
    """No 'openai' provider_config exists to embed with (#98 v1's only
    supported embedding provider)."""


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    # OpenAI's embeddings usage has no output/completion leg -- this is
    # the whole bill for the call (see pricing.py's 0.0 output rate for
    # this model).
    prompt_tokens: int


async def embed_texts(db: AsyncSession, texts: list[str]) -> EmbeddingResult:
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
    return EmbeddingResult(
        vectors=[item.embedding for item in response.data],
        prompt_tokens=response.usage.prompt_tokens,
    )


async def record_embedding_run(db: AsyncSession, prompt_tokens: int) -> AgentRun:
    """Persists one ingest call's embedding spend as an AgentRun, via the
    same `record_agent_run` choke point every other billed call in this
    codebase uses -- see this module's docstring for why. `run_output` is
    a duck-typed stand-in (`record_agent_run` only reads `.metrics.
    input_tokens/output_tokens/total_tokens` and, via `log_tool_calls`,
    `.tools`, which this correctly leaves absent -- an embedding call
    makes no tool calls).

    Lazy import: `rivulets.agentos`'s package init pulls in
    agentos.service -> agentos.tool_resolution ->
    tools/builtin/knowledge_base.py, which imports this module for
    `embed_query_sync` -- a module-level import of agentos.accounting
    here would be circular whenever something imports this module before
    `rivulets.agentos` has already been loaded."""
    from rivulets.agentos.accounting import record_agent_run

    run_output = SimpleNamespace(
        metrics=SimpleNamespace(
            input_tokens=prompt_tokens, output_tokens=0, total_tokens=prompt_tokens
        )
    )
    return await record_agent_run(
        db,
        None,
        EMBEDDING_PROVIDER_MODEL,
        None,
        "completed",
        run_output,  # pyright: ignore[reportArgumentType]
        source="embedding",
    )


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
