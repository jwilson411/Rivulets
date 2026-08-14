"""Knowledge base CRUD and document ingestion (api/knowledge_bases.py, #98)
through the real HTTP API. Embedding calls are monkeypatched to a
deterministic fake -- no real OpenAI network access in the test suite.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rivulets.db.models import File as FileRow
from rivulets.db.models import SyncPendingOutbound
from rivulets.db.session import session_scope


def _create_agent(client: TestClient, headers: dict[str, str], name: str = "KB Agent") -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "Test agent",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    return agent_id


def _upload(
    client: TestClient, headers: dict[str, str], name: str, content: bytes, mime: str = "text/plain"
) -> str:
    response = client.post(
        "/api/v1/files/upload",
        files={"upload": (name, content, mime)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    file_id: str = response.json()["file_id"]
    return file_id


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    async def _fake_embed_texts(_db: object, texts: list[str]) -> list[list[float]]:
        # Deterministic per-text vector so cosine-similarity ranking is
        # exercisable without a real embedding model.
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    monkeypatch.setattr("rivulets.api.knowledge_bases.embed_texts", _fake_embed_texts)


def test_create_knowledge_base_requires_agent_or_team_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # scope_type='agent' with no agent_id fails pydantic validation before
    # ever reaching the DB -- same "exactly one subject" precedent
    # BudgetCapCreate/EvalSuiteCreate already established.
    response = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent"},
        headers=auth_headers,
    )
    assert response.status_code == 422, response.text


def test_create_agent_scoped_knowledge_base_and_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Product Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scope_type"] == "agent"
    assert body["agent_id"] == agent_id
    assert body["team_id"] is None
    assert body["document_count"] == 0

    listed = client.get("/api/v1/knowledge-bases", headers=auth_headers)
    assert listed.status_code == 200
    assert [kb["id"] for kb in listed.json()] == [body["id"]]


def test_create_knowledge_base_404s_for_unknown_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_get_knowledge_base_returns_404_for_unknown_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/knowledge-bases/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_update_name_and_description(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Old", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    )
    kb_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/knowledge-bases/{kb_id}",
        json={"name": "New", "description": "Now with a description"},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "New"
    assert updated.json()["description"] == "Now with a description"


def test_delete_knowledge_base_removes_it(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Doomed", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    )
    kb_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers).status_code == 404


async def test_delete_knowledge_base_queues_sync_tombstone(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#287: the `client` fixture never actually starts the sync engine, so
    a successful delete queues a tombstone retry (SyncPendingOutbound.
    deleted=True) instead of the delete never reaching any peer at all --
    mirrors test_teams_api.py's equivalent for delete_team."""
    agent_id = _create_agent(client, auth_headers)
    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Doomed Sync", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    )
    kb_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert deleted.status_code == 204

    async with session_scope() as db:
        pending = await db.get(SyncPendingOutbound, ("knowledge_base", kb_id))
        assert pending is not None
        assert pending.deleted is True


def test_ingest_document_chunks_and_embeds_a_text_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    file_id = _upload(client, auth_headers, "notes.txt", b"x" * 2500)

    ingested = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    )
    assert ingested.status_code == 201, ingested.text
    body = ingested.json()
    assert body["status"] == "ingested"
    # 2500 chars at chunk_size=1000/overlap=100 -> chunks at [0:1000),
    # [900:1900), [1800:2500) = 3 chunks.
    assert body["chunk_count"] == 3

    listed = client.get(f"/api/v1/knowledge-bases/{kb_id}/documents", headers=auth_headers)
    assert listed.status_code == 200
    assert [d["id"] for d in listed.json()] == [body["id"]]

    kb_after = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert kb_after.json()["document_count"] == 1


def test_ingest_document_rejects_non_text_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    file_id = _upload(client, auth_headers, "photo.png", b"\x89PNG\r\n\x1a\n", mime="image/png")

    response = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "text" in response.text.lower()


def test_ingest_document_without_embedding_provider_fails_clearly(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Undo this file's autouse fake to exercise the real
    # NoEmbeddingProviderError path (no 'openai' provider_config exists
    # in this test's DB).
    from rivulets.knowledge_base.embeddings import embed_texts

    monkeypatch.setattr("rivulets.api.knowledge_bases.embed_texts", embed_texts)

    agent_id = _create_agent(client, auth_headers)
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    file_id = _upload(client, auth_headers, "notes.txt", b"hello world")

    response = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "OpenAI provider configured" in response.text

    documents = client.get(
        f"/api/v1/knowledge-bases/{kb_id}/documents", headers=auth_headers
    ).json()
    assert documents[0]["status"] == "failed"


async def test_ingest_document_ignores_a_stale_local_path_pointing_outside_files_dir(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """#288: File.local_path was previously trusted verbatim here. A row
    whose local_path was written by older/buggy code (or a peer before
    the #239 apply fix) must not have that path followed -- ingest should
    re-derive the path from files_dir + content_hash the same way
    api/files.py's download_file already does, so a local_path like
    /etc/passwd is never opened."""
    secret = tmp_path / "secret.txt"
    secret.write_text("this must never be ingested")

    agent_id = _create_agent(client, auth_headers)
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    file_id = _upload(client, auth_headers, "notes.txt", b"hello world")

    async with session_scope() as db:
        row = await db.get(FileRow, file_id)
        assert row is not None
        row.local_path = str(secret)
        await db.commit()

    response = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    )

    # The real files_dir path (from content_hash) still holds the
    # originally-uploaded "hello world" bytes, so ingest succeeds -- just
    # never against the corrupted local_path.
    assert response.status_code == 201, response.text


def test_delete_document_removes_it(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    file_id = _upload(client, auth_headers, "notes.txt", b"hello world")
    document_id = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    ).json()["id"]

    deleted = client.delete(
        f"/api/v1/knowledge-bases/{kb_id}/documents/{document_id}", headers=auth_headers
    )
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/v1/knowledge-bases/{kb_id}/documents", headers=auth_headers).json() == []
    )
