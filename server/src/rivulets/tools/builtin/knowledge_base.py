"""Knowledge base search built-in tool (#98).

Lets an invoked agent search a knowledge base it (or its team) owns,
mid-conversation, the same way it calls any other tool. Read-only --
deliberately excluded from SENSITIVE_BUILTIN_TOOL_NAMES
(agentos/tool_resolution.py) like read_attached_file/list_files, since it
can't mutate anything, only read chunks this workspace already ingested.

Like files.py/db_query.py, this opens the workspace DB read-only via raw
sqlite3 rather than the async engine, since tools run synchronously
inside agno's tool-call loop. Ranking is brute-force cosine similarity in
Python (see KnowledgeBaseChunk's docstring in db/models.py for why) --
fine at the modest chunk counts a v1, single-file-per-document knowledge
base actually reaches.
"""

import json
import sqlite3

from agno.tools import tool

from rivulets.config import get_settings
from rivulets.knowledge_base.embeddings import (
    NoEmbeddingProviderError,
    cosine_similarity,
    embed_query_sync,
)

_DEFAULT_TOP_K = 5
_MAX_TOP_K = 20


@tool
def search_knowledge_base(knowledge_base_id: str, query: str, top_k: int = _DEFAULT_TOP_K) -> str:
    """Search a knowledge base for chunks most relevant to `query` and
    return up to `top_k` results (default 5, capped at 20) as text
    snippets with their source filename."""
    top_k = max(1, min(top_k, _MAX_TOP_K))

    db_path = get_settings().db_path
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        kb_row = conn.execute(
            "SELECT id FROM knowledge_base WHERE id = ?", (knowledge_base_id,)
        ).fetchone()
        if kb_row is None:
            raise ValueError(f"No knowledge base found with id {knowledge_base_id!r}")

        rows = conn.execute(
            """
            SELECT c.content, c.embedding_json, f.filename
            FROM knowledge_base_chunk c
            JOIN knowledge_base_document d ON d.id = c.document_id
            JOIN file f ON f.id = d.file_id
            WHERE c.knowledge_base_id = ?
            """,
            (knowledge_base_id,),
        ).fetchall()

    if not rows:
        return "This knowledge base has no ingested documents yet."

    try:
        query_vector = embed_query_sync(query)
    except NoEmbeddingProviderError as exc:
        raise ValueError(str(exc)) from exc

    scored = sorted(
        (
            (cosine_similarity(query_vector, json.loads(embedding_json)), content, filename)
            for content, embedding_json, filename in rows
        ),
        key=lambda item: item[0],
        reverse=True,
    )[:top_k]

    return "\n\n".join(
        f"[{filename}] (score {score:.3f})\n{content}" for score, content, filename in scored
    )
