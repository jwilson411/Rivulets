"""search_knowledge_base builtin tool (tools/builtin/knowledge_base.py,
#98). Like test_files_tool.py, this tool opens get_settings().db_path
directly with sqlite3 rather than through the app's async SQLAlchemy
engine, so tests seed real `knowledge_base`/`knowledge_base_document`/
`knowledge_base_chunk`/`file` rows in that real (per-session tempdir)
file rather than using the `client`/`db_session` fixtures.
"""

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from typing import cast

import pytest

from rivulets.config import get_settings
from rivulets.tools.builtin.knowledge_base import search_knowledge_base

assert search_knowledge_base.entrypoint is not None
_call = cast("Callable[..., str]", search_knowledge_base.entrypoint)


def _fixed_query_embedding(_query: str) -> list[float]:
    return [1.0, 0.0, 0.0]


@pytest.fixture
def kb_db() -> Iterator[None]:
    db_path = get_settings().db_path
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE knowledge_base (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE knowledge_base_document (id TEXT PRIMARY KEY, file_id TEXT)")
        conn.execute(
            "CREATE TABLE knowledge_base_chunk (id TEXT PRIMARY KEY, knowledge_base_id TEXT, "
            "document_id TEXT, chunk_index INTEGER, content TEXT, embedding_json TEXT)"
        )
        conn.execute("CREATE TABLE file (id TEXT PRIMARY KEY, filename TEXT)")
        conn.commit()
    finally:
        conn.close()
    yield
    db_path.unlink(missing_ok=True)


def _seed_chunk(
    kb_id: str, document_id: str, file_id: str, filename: str, content: str, embedding: list[float]
) -> None:
    conn = sqlite3.connect(get_settings().db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO knowledge_base (id) VALUES (?)", (kb_id,))
        conn.execute("INSERT OR IGNORE INTO file (id, filename) VALUES (?, ?)", (file_id, filename))
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_base_document (id, file_id) VALUES (?, ?)",
            (document_id, file_id),
        )
        conn.execute(
            "INSERT INTO knowledge_base_chunk "
            "(id, knowledge_base_id, document_id, chunk_index, content, embedding_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), kb_id, document_id, 0, content, json.dumps(embedding)),
        )
        conn.commit()
    finally:
        conn.close()


def test_unknown_knowledge_base_id_raises(kb_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rivulets.tools.builtin.knowledge_base.embed_query_sync", _fixed_query_embedding
    )
    with pytest.raises(ValueError, match="No knowledge base found"):
        _call(knowledge_base_id="does-not-exist", query="anything")


def test_empty_knowledge_base_returns_a_clear_message(
    kb_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.tools.builtin.knowledge_base.embed_query_sync", _fixed_query_embedding
    )
    conn = sqlite3.connect(get_settings().db_path)
    conn.execute("INSERT INTO knowledge_base (id) VALUES ('kb-1')")
    conn.commit()
    conn.close()

    result = _call(knowledge_base_id="kb-1", query="anything")
    assert result == "This knowledge base has no ingested documents yet."


def test_returns_chunks_ranked_by_similarity(kb_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_chunk("kb-1", "doc-1", "file-1", "close.txt", "closely related content", [1.0, 0.0, 0.0])
    _seed_chunk("kb-1", "doc-1", "file-1", "close.txt", "off-topic content", [0.0, 1.0, 0.0])
    monkeypatch.setattr(
        "rivulets.tools.builtin.knowledge_base.embed_query_sync", _fixed_query_embedding
    )

    result = _call(knowledge_base_id="kb-1", query="tell me about the topic")
    lines = result.split("\n\n")
    assert len(lines) == 2
    assert "closely related content" in lines[0]
    assert "score 1.000" in lines[0]
    assert "off-topic content" in lines[1]
    assert "score 0.000" in lines[1]


def test_respects_top_k(kb_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        _seed_chunk("kb-1", "doc-1", "file-1", "notes.txt", f"chunk {i}", [1.0, 0.0, 0.0])
    monkeypatch.setattr(
        "rivulets.tools.builtin.knowledge_base.embed_query_sync", _fixed_query_embedding
    )

    result = _call(knowledge_base_id="kb-1", query="q", top_k=2)
    assert len(result.split("\n\n")) == 2


def test_no_embedding_provider_raises_value_error(
    kb_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rivulets.knowledge_base.embeddings import NoEmbeddingProviderError

    _seed_chunk("kb-1", "doc-1", "file-1", "notes.txt", "content", [1.0, 0.0, 0.0])

    def _raise(_query: str) -> list[float]:
        raise NoEmbeddingProviderError("no provider configured")

    monkeypatch.setattr("rivulets.tools.builtin.knowledge_base.embed_query_sync", _raise)

    with pytest.raises(ValueError, match="no provider configured"):
        _call(knowledge_base_id="kb-1", query="q")
