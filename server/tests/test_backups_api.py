"""api/backups.py — manual backup, listing, and restore over HTTP.

Automatic (daily/pre-upgrade) snapshots are covered by test_backup.py and
are no-op'd for the `client` fixture (see conftest.py) — this file only
exercises what a user triggers directly.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rivulets.agentos import get_agentos
from rivulets.agentos.tool_resolution import resolve_agent_tools
from rivulets.backup import BackupIntegrityError
from rivulets.db.models import Agent, AgentTool
from rivulets.db.session import session_scope


def test_create_manual_backup_returns_metadata(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/backups", headers=auth_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "manual"
    assert body["filename"].startswith("manual-")
    assert body["size_bytes"] > 0
    assert body["created_at"]


def test_list_backups_includes_created_manual_backup(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/backups", headers=auth_headers).json()

    response = client.get("/api/v1/backups", headers=auth_headers)

    assert response.status_code == 200
    filenames = {b["filename"] for b in response.json()}
    assert created["filename"] in filenames


def test_backups_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/backups").status_code == 401
    assert client.post("/api/v1/backups").status_code == 401
    assert (
        client.post(
            "/api/v1/backups/whatever.db/restore", json={"confirm_filename": "whatever.db"}
        ).status_code
        == 401
    )


def test_restore_unknown_backup_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/backups/does-not-exist.db/restore",
        json={"confirm_filename": "does-not-exist.db"},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_restore_rejects_path_outside_backups_dir(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # %2e%2e is a single non-"/" path segment at the routing layer (so it
    # actually reaches this handler, unlike a literal ".." which the HTTP
    # client normalizes away before the request is even sent) that decodes
    # to ".." once FastAPI hands it to the handler as `filename` — it
    # resolves back to backups_dir itself, a directory not a file, so
    # resolve_backup_path rejects it the same way an out-of-tree path would.
    response = client.post(
        "/api/v1/backups/%2e%2e/restore", json={"confirm_filename": ".."}, headers=auth_headers
    )
    assert response.status_code == 404


def test_restore_rejects_mismatched_confirmation(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    backup = client.post("/api/v1/backups", headers=auth_headers).json()
    before = {b["filename"] for b in client.get("/api/v1/backups", headers=auth_headers).json()}

    response = client.post(
        f"/api/v1/backups/{backup['filename']}/restore",
        json={"confirm_filename": "not-the-right-filename.tar"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    # Not applied — no pre-restore safety snapshot should have been taken,
    # since the restore never got as far as touching any files.
    after = {b["filename"] for b in client.get("/api/v1/backups", headers=auth_headers).json()}
    assert after == before


def test_restore_reverts_workspace_state_to_snapshot(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    backup = client.post("/api/v1/backups", headers=auth_headers).json()

    added = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-test"},
        headers=auth_headers,
    )
    assert added.status_code == 201, added.text
    assert len(client.get("/api/v1/providers", headers=auth_headers).json()) == 1

    restore = client.post(
        f"/api/v1/backups/{backup['filename']}/restore",
        json={"confirm_filename": backup["filename"]},
        headers=auth_headers,
    )
    assert restore.status_code == 204, restore.text

    after = client.get("/api/v1/providers", headers=auth_headers)
    assert after.status_code == 200, after.text
    assert after.json() == []


def test_restore_takes_pre_restore_safety_snapshot(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    backup = client.post("/api/v1/backups", headers=auth_headers).json()

    restore = client.post(
        f"/api/v1/backups/{backup['filename']}/restore",
        json={"confirm_filename": backup["filename"]},
        headers=auth_headers,
    )
    assert restore.status_code == 204, restore.text

    # Existence, not an exact before/after delta: this endpoint's
    # get_settings() is the process-wide lru_cache'd singleton, shared with
    # every other test in the session (test_backup.py's precise pairing —
    # exact filenames, exact kinds — instead uses its own tmp_path Settings
    # and a frozen clock), so another test's restore landing in the same
    # wall-clock second could legitimately overwrite rather than add a
    # same-named pre-restore-<timestamp>.tar here.
    kinds = {b["kind"] for b in client.get("/api/v1/backups", headers=auth_headers).json()}
    assert "pre-restore" in kinds


def test_restore_resyncs_agentos_so_the_restored_agent_is_runnable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#243: restore must rebuild AgentOS's in-process registry from the
    restored rivulets.db, not just swap the DB file — otherwise an agent
    that only existed pre-restore stays invokable (stale registry) and one
    that only exists post-restore isn't (never registered)."""
    client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-test"},
        headers=auth_headers,
    )
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "restored-agent",
            "description": "A test agent",
            "instructions": "You are a helpful assistant.",
            "model": "openai:gpt-4o-mini",
        },
        headers=auth_headers,
    ).json()
    # A real ProviderConfig is in place, so resolve_model() succeeds and
    # the agent actually made it into AgentOS's registry.
    assert agent["agentos_agent_id"] == agent["id"]

    backup = client.post("/api/v1/backups", headers=auth_headers).json()

    # Create a second agent *after* the snapshot — restoring should make
    # this one disappear from AgentOS along with its DB row.
    post_snapshot_agent = client.post(
        "/api/v1/agents",
        json={
            "name": "post-snapshot-agent",
            "description": "A test agent",
            "instructions": "You are a helpful assistant.",
            "model": "openai:gpt-4o-mini",
        },
        headers=auth_headers,
    ).json()

    restore = client.post(
        f"/api/v1/backups/{backup['filename']}/restore",
        json={"confirm_filename": backup["filename"]},
        headers=auth_headers,
    )
    assert restore.status_code == 204, restore.text

    agent_ids_in_registry = {a.id for a in get_agentos().agents or []}
    assert agent["id"] in agent_ids_in_registry
    assert post_snapshot_agent["id"] not in agent_ids_in_registry


async def test_restore_brings_back_custom_tool_source_so_it_still_execs(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#290: restore must bring `tools/` back along with `rivulets.db` --
    otherwise the restored Tool row's source_path points at a file that no
    longer exists, and _load_custom_tool (agentos/tool_resolution.py)
    silently skips it instead of raising, so every invocation of a custom
    tool that existed at backup time would quietly become a no-op."""
    create = client.post(
        "/api/v1/tools",
        json={"name": "greet", "description": "Greets someone."},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    tool = create.json()

    source = (
        "from agno.tools import tool\n\n\n"
        "@tool\n"
        "def greet(name: str) -> str:\n"
        '    return f"Hello, {name}!"\n'
    )
    saved = client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        json={"source_code": source},
        headers=auth_headers,
    )
    assert saved.status_code == 201, saved.text

    backup = client.post("/api/v1/backups", headers=auth_headers).json()

    # Simulate the on-disk source being gone by the time of restore (e.g.
    # a restore onto replacement hardware where only the backup archive
    # travels with the workspace) -- the archive, not the live file, must
    # be what restore relies on.
    Path(tool["source_path"]).unlink()

    restore = client.post(
        f"/api/v1/backups/{backup['filename']}/restore",
        json={"confirm_filename": backup["filename"]},
        headers=auth_headers,
    )
    assert restore.status_code == 204, restore.text
    assert Path(tool["source_path"]).read_text() == source

    async with session_scope() as db:
        agent = Agent(
            id="tool-roundtrip-agent",
            name="Tool Roundtrip Agent",
            description="A test agent for the tool restore round-trip.",
            instructions="Do the thing.",
            model="openai:gpt-4o-mini",
        )
        db.add(agent)
        await db.commit()
        db.add(AgentTool(agent_id=agent.id, tool_id=tool["id"]))
        await db.commit()

        resolved = await resolve_agent_tools(db, agent)

    assert len(resolved) == 1
    assert resolved[0].entrypoint is not None
    assert resolved[0].entrypoint("World") == "Hello, World!"


def test_create_manual_backup_returns_500_on_integrity_failure(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_create_backup(*_args: object, **_kwargs: object) -> None:
        raise BackupIntegrityError("VACUUM INTO snapshot failed integrity_check")

    monkeypatch.setattr("rivulets.api.backups.create_backup", fake_create_backup)

    response = client.post("/api/v1/backups", headers=auth_headers)

    assert response.status_code == 500
    assert "integrity_check" in response.json()["detail"]
