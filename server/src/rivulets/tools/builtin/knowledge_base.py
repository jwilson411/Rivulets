"""Knowledge base search built-in tool (#98).

Lets an invoked agent search a knowledge base it (or its team) owns,
mid-conversation, the same way it calls any other tool. Read-only --
deliberately excluded from SENSITIVE_BUILTIN_TOOL_NAMES
(agentos/tool_resolution.py) like read_attached_file/list_files, since it
can't mutate anything, only read chunks this workspace already ingested.

#327: the docstring above always promised "it (or its team) owns", but
the actual check used to be "the id exists, period" -- any agent this
tool was assigned to (unscoped: no BUILTIN_TOOL_SCOPES entry, so no
owner grant is even needed to assign it) could dump any knowledge base in
the workspace by id, including ones scoped to a different, more
privileged agent. `agent` below is the calling AgnoAgent -- injected by
agno itself (agno/agent/_tools.py sets `Function._agent` to a fresh
per-run deep copy before each call, and function.py auto-passes it into
any entrypoint parameter literally named `agent`), not something this
tool resolves on its own. That's what makes real ownership enforcement
possible here without a required_scope: given real enforcement, an
invite-grant session assigning this tool to a self-authored agent only
ever lets that agent read knowledge bases it (or its team) actually owns
-- the same reach `list_channels`/`list_agents` are already trusted with
unscoped, not a way to dump someone else's knowledge base anymore.

Like files.py/db_query.py, this opens the workspace DB read-only via raw
sqlite3 rather than the async engine, since tools run synchronously
inside agno's tool-call loop. Ranking is brute-force cosine similarity in
Python (see KnowledgeBaseChunk's docstring in db/models.py for why) --
fine at the modest chunk counts a v1, single-file-per-document knowledge
base actually reaches.
"""

import json
import sqlite3

from agno.agent import Agent as AgnoAgent
from agno.tools import tool

from rivulets.config import get_settings
from rivulets.knowledge_base.embeddings import (
    NoEmbeddingProviderError,
    cosine_similarity,
    embed_query_sync,
)

_DEFAULT_TOP_K = 5
_MAX_TOP_K = 20

_NOT_FOUND = "No knowledge base found with id {kb_id!r}"


@tool
def search_knowledge_base(
    agent: AgnoAgent, knowledge_base_id: str, query: str, top_k: int = _DEFAULT_TOP_K
) -> str:
    """Search a knowledge base for chunks most relevant to `query` and
    return up to `top_k` results (default 5, capped at 20) as text
    snippets with their source filename. Only knowledge bases this agent
    (or its team) owns are searchable."""
    top_k = max(1, min(top_k, _MAX_TOP_K))

    db_path = get_settings().db_path
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        kb_row = conn.execute(
            "SELECT scope_type, agent_id, team_id FROM knowledge_base WHERE id = ?",
            (knowledge_base_id,),
        ).fetchone()
        if kb_row is None:
            raise ValueError(_NOT_FOUND.format(kb_id=knowledge_base_id))
        scope_type, kb_agent_id, kb_team_id = kb_row

        # Same "id exists is not ownership" gap #327 closed on the HTTP
        # side (api/knowledge_bases.py) -- checked here too since this
        # tool reads straight from sqlite, not through that router.
        if scope_type == "agent":
            owns = kb_agent_id == agent.id
        else:
            owns = (
                conn.execute(
                    "SELECT 1 FROM team_agent WHERE team_id = ? AND agent_id = ?",
                    (kb_team_id, agent.id),
                ).fetchone()
                is not None
            )
        if not owns:
            # Same message as the true-404 case above -- an agent probing
            # ids it doesn't own shouldn't be able to distinguish
            # "doesn't exist" from "exists but isn't yours".
            raise ValueError(_NOT_FOUND.format(kb_id=knowledge_base_id))

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
