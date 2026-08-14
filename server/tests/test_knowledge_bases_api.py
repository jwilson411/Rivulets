"""Knowledge base CRUD and document ingestion (api/knowledge_bases.py, #98)
through the real HTTP API. Embedding calls are monkeypatched to a
deterministic fake -- no real OpenAI network access in the test suite.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from rivulets.db.models import AgentRun, SyncPendingOutbound
from rivulets.db.models import File as FileRow
from rivulets.db.session import session_scope
from rivulets.knowledge_base.embeddings import EmbeddingResult


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
    async def _fake_embed_texts(_db: object, texts: list[str]) -> EmbeddingResult:
        # Deterministic per-text vector so cosine-similarity ranking is
        # exercisable without a real embedding model.
        return EmbeddingResult(
            vectors=[[float(len(t)), 1.0, 0.0] for t in texts],
            prompt_tokens=sum(len(t) for t in texts),
        )

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


async def test_ingest_document_records_embedding_spend_as_an_agent_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#320: ingestion's embedding call was invisible to the usage
    dashboard/budget caps before this -- no AgentRun was ever created for
    it. agent_id is None (same #246 dispatcher_call precedent as any
    other spend that can't be attributed to a single agent invocation)."""
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

    async with session_scope() as db:
        run = (
            (await db.execute(select(AgentRun).where(AgentRun.source == "embedding")))
            .scalars()
            .one()
        )
    assert run.agent_id is None
    assert run.model == "openai:text-embedding-3-small"
    assert run.cost_usd is not None and run.cost_usd > 0


async def test_ingest_document_blocked_by_a_tripped_hard_stop_budget_cap(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#320: a KB ingest embeds against the owner's OpenAI key same as any
    other billed call -- #246 only wired dispatch/budgets.py's check into
    channel dispatch, leaving ingestion able to keep spending past a
    tripped workspace hard_stop cap."""
    agent_id = _create_agent(client, auth_headers)
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    created = client.post(
        "/api/v1/budgets",
        json={"scope_type": "workspace", "period": "day", "limit_usd": 0.01, "action": "hard_stop"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text

    async with session_scope() as db:
        db.add(AgentRun(agent_id=None, model="openai:gpt-4o", status="completed", cost_usd=1.0))
        await db.commit()

    file_id = _upload(client, auth_headers, "notes.txt", b"x" * 2500)
    response = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    )
    assert response.status_code == 402, response.text
    assert "budget cap" in response.text.lower()

    documents = client.get(
        f"/api/v1/knowledge-bases/{kb_id}/documents", headers=auth_headers
    ).json()
    assert documents[0]["status"] == "failed"


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


# #327: invite-grant confused-deputy paths -- see api/knowledge_bases.py's
# module docstring and _require_owner_for_scoped_kb.


def _invite_headers(client: TestClient, auth_headers: dict[str, str]) -> dict[str, str]:
    created_invite = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    invite_token = created_invite["url"].rsplit("/", 1)[-1]
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": invite_token, "display_name": "Guest"},
    ).json()
    return {"Authorization": f"Bearer {accepted['token']}"}


def _grant_scope(client: TestClient, auth_headers: dict[str, str], agent_id: str) -> None:
    granted = client.put(
        f"/api/v1/agents/{agent_id}/tool-scopes",
        json={"scopes": ["settings:manage"]},
        headers=auth_headers,
    )
    assert granted.status_code == 200, granted.text


def test_invite_grant_cannot_create_kb_scoped_to_a_privileged_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invite_headers = _invite_headers(client, auth_headers)
    agent_id = _create_agent(client, auth_headers, "Privileged Agent")
    _grant_scope(client, auth_headers, agent_id)

    response = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Poison", "scope_type": "agent", "agent_id": agent_id},
        headers=invite_headers,
    )
    assert response.status_code == 403, response.text


def test_invite_grant_can_create_kb_scoped_to_an_unprivileged_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Routine, unprivileged agent/team scoping stays open to any grant --
    the same general openness Team/Agent/Tool CRUD already have."""
    invite_headers = _invite_headers(client, auth_headers)
    agent_id = _create_agent(client, auth_headers, "Plain Agent")

    response = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=invite_headers,
    )
    assert response.status_code == 201, response.text


def test_invite_grant_cannot_ingest_into_kb_scoped_to_a_privileged_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invite_headers = _invite_headers(client, auth_headers)
    agent_id = _create_agent(client, auth_headers, "Privileged Agent")
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    _grant_scope(client, auth_headers, agent_id)
    file_id = _upload(client, invite_headers, "notes.txt", b"instructions to poison the agent")

    response = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=invite_headers,
    )
    assert response.status_code == 403, response.text


def test_invite_grant_cannot_delete_kb_scoped_to_a_privileged_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invite_headers = _invite_headers(client, auth_headers)
    agent_id = _create_agent(client, auth_headers, "Privileged Agent")
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Production Corpus", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    _grant_scope(client, auth_headers, agent_id)

    response = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=invite_headers)
    assert response.status_code == 403, response.text
    assert client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers).status_code == 200


def test_invite_grant_cannot_delete_document_in_kb_scoped_to_a_privileged_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invite_headers = _invite_headers(client, auth_headers)
    agent_id = _create_agent(client, auth_headers, "Privileged Agent")
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
    _grant_scope(client, auth_headers, agent_id)

    response = client.delete(
        f"/api/v1/knowledge-bases/{kb_id}/documents/{document_id}", headers=invite_headers
    )
    assert response.status_code == 403, response.text


def test_invite_grant_cannot_write_to_team_scoped_kb_with_a_privileged_member(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """A team-scoped knowledge base is reachable by every member agent, so
    one scoped member (not necessarily the team's only agent) is enough to
    make a non-owner write to it a confused-deputy risk too."""
    invite_headers = _invite_headers(client, auth_headers)
    team_id = client.post("/api/v1/teams", json={"name": "Ops"}, headers=auth_headers).json()["id"]
    privileged_agent_id = _create_agent(client, auth_headers, "Privileged Team Agent")
    _grant_scope(client, auth_headers, privileged_agent_id)
    patched = client.patch(
        f"/api/v1/agents/{privileged_agent_id}",
        json={"team_ids": [team_id]},
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Team Docs", "scope_type": "team", "team_id": team_id},
        headers=auth_headers,
    ).json()["id"]

    response = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=invite_headers)
    assert response.status_code == 403, response.text


def test_owner_grant_can_still_write_to_kb_scoped_to_a_privileged_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The gate is non-owner-only -- an owner session must keep the full
    reach it always had over its own privileged agents' knowledge bases."""
    agent_id = _create_agent(client, auth_headers, "Privileged Agent")
    _grant_scope(client, auth_headers, agent_id)

    kb_id = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Docs", "scope_type": "agent", "agent_id": agent_id},
        headers=auth_headers,
    ).json()["id"]
    file_id = _upload(client, auth_headers, "notes.txt", b"hello world")
    ingested = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        json={"file_id": file_id},
        headers=auth_headers,
    )
    assert ingested.status_code == 201, ingested.text

    deleted = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert deleted.status_code == 204
