"""#237: post_message must not hold a SQLite write lock across an agent's
LLM call. Deliberately doesn't use conftest.py's `client`/`db_session`
fixtures -- both point at an `in_memory=True` engine on a StaticPool,
which hands every session in the process the *same* literal sqlite3
connection. Real cross-connection lock contention (what actually paged
this bug in production) can't happen on a single shared connection, so
that fixture would pass even with the bug present. This uses a real
on-disk database file instead, exactly like the issue asks for.

`busy_timeout` is set much shorter here (50ms) than the app's own 5000ms
(db/session.py) so the two scenarios below stay fast and deterministic:
under the pre-#237 code, one request's held-open write transaction easily
outlasts 50ms of a concurrent request's own retry window, so the second
request's write reliably fails with "database is locked" well within a
fraction of a second, without needing a multi-second sleep to prove it.
"""

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient
from sqlalchemy import event

from rivulets.agentos.service import init_agentos, reset_agentos_for_testing
from rivulets.app import create_app
from rivulets.config import Settings
from rivulets.db.session import get_engine, init_db, make_engine, override_engine
from rivulets.security.rate_limit import (
    get_invite_accept_rate_limiter,
    get_login_rate_limiter,
    get_webhook_trigger_rate_limiter,
)
from rivulets.security.session import get_session_key_store
from rivulets.security.webhook_signing import get_webhook_replay_guard
from rivulets.sync.engine import SyncEngine, reset_sync_engine_for_testing

_SHORT_BUSY_TIMEOUT_MS = 50


async def _noop_async(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
async def disk_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[TestClient]:
    """Same shape as conftest.py's `client` fixture, but wired to a real
    on-disk SQLite file with a short busy_timeout (see module docstring)
    instead of the in-memory/StaticPool engine that fixture hardcodes."""
    monkeypatch.setattr(SyncEngine, "start", _noop_async)
    monkeypatch.setattr(SyncEngine, "stop", _noop_async)
    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.app.run_startup_backup_checks", _noop_async)
    monkeypatch.setattr("rivulets.app.run_migrations", init_db)
    monkeypatch.setattr("rivulets.app.run_scheduler_loop", _noop_async)
    monkeypatch.setattr("rivulets.app.run_retention_loop", _noop_async)

    settings = Settings(workspace_dir=tmp_path / "workspace")
    settings.ensure_workspace_dirs()
    engine = make_engine(settings)

    def _shorten_busy_timeout(dbapi_connection: Any, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout={_SHORT_BUSY_TIMEOUT_MS}")
        cursor.close()

    # Runs after db/session.py's own connect listener (registered first,
    # by make_engine itself), so this simply overrides its busy_timeout=5000
    # with a much shorter one for this test only.
    event.listen(engine.sync_engine, "connect", _shorten_busy_timeout)

    override_engine(engine)
    await init_db(engine)
    reset_agentos_for_testing()
    init_agentos()
    get_login_rate_limiter().reset_for_testing()
    get_invite_accept_rate_limiter().reset_for_testing()
    get_webhook_trigger_rate_limiter().reset_for_testing()
    get_webhook_replay_guard().reset_for_testing()

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_session_key_store().clear()
    await get_engine().dispose()
    override_engine(None)
    reset_agentos_for_testing()
    reset_sync_engine_for_testing()
    get_login_rate_limiter().reset_for_testing()
    get_invite_accept_rate_limiter().reset_for_testing()
    get_webhook_trigger_rate_limiter().reset_for_testing()
    get_webhook_replay_guard().reset_for_testing()


def _login(client: TestClient, display_name: str) -> dict[str, str]:
    from rivulets.security import keys

    mnemonic = keys.generate_mnemonic()
    response = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert response.status_code == 200, response.text
    token: str = response.json()["token"]
    identity_response = client.post(
        "/api/v1/auth/identity",
        json={"display_name": display_name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert identity_response.status_code == 200, identity_response.text
    token = identity_response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_channel_with_slow_agent(
    client: TestClient, headers: dict[str, str], delay_seconds: float
) -> str:
    """A "keyword" rule (not "always") matching only the concurrency test's
    own trigger word ("go"), so the seed message used to create each
    rivulet below doesn't itself dispatch the agent -- keeps the setup
    phase (before the concurrent phase starts) free of any LLM call."""
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Slow Responder",
            "description": "Takes a moment to respond, for #237's lock test.",
            "instructions": "Say OK.",
            "model": "anthropic:claude-3-5-haiku-latest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    rules = client.patch(
        f"/api/v1/agents/{agent_id}/routing-rules",
        json={"rules": [{"rule_type": "keyword", "pattern": '["go"]', "priority": 0}]},
        headers=headers,
    )
    assert rules.status_code == 200, rules.text

    team = client.post("/api/v1/teams", json={"name": "Lock Test Team"}, headers=headers)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    client.patch(f"/api/v1/teams/{team_id}", json={"agent_ids": [agent_id]}, headers=headers)

    channel = client.post("/api/v1/channels", json={"name": "lock-test"}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id: str = channel.json()["id"]
    updated = client.patch(
        f"/api/v1/channels/{channel_id}", json={"team_id": team_id}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    return channel_id


def test_two_overlapping_post_message_calls_on_disk_do_not_lock(
    disk_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two humans posting into two *different* rivulets while one agent is
    "thinking" (#237's reported scenario) -- SQLite's write lock is
    file-level, not per-row/table, so this doesn't even need the two
    requests to touch the same rivulet to reproduce the original bug: any
    concurrent write anywhere in the file used to fail while dispatch held
    its transaction open across the mocked LLM call's delay below.
    """
    delay_seconds = 0.15

    async def slow_run_agent(*_args: object, **_kwargs: object) -> Any:
        await asyncio.sleep(delay_seconds)
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "OK."
        )

    monkeypatch.setattr("rivulets.dispatch.service.run_agent", slow_run_agent)

    headers = _login(disk_client, "Test User")
    channel_id = _create_channel_with_slow_agent(disk_client, headers, delay_seconds)

    rivulet_ids: list[str] = []
    for _ in range(2):
        created = disk_client.post(
            f"/api/v1/channels/{channel_id}/rivulets",
            json={"content": "hello there"},  # doesn't match the "go" keyword rule
            headers=headers,
        )
        assert created.status_code == 201, created.text
        rivulet_ids.append(created.json()["id"])

    def _post_trigger(rivulet_id: str) -> Any:
        return disk_client.post(
            f"/api/v1/rivulets/{rivulet_id}/messages",
            json={"content": "go do it"},
            headers=headers,
        )

    # Two real, concurrently-overlapping requests against the same on-disk
    # database file: TestClient runs the (async) app on one shared
    # background event loop, so two blocking .post() calls issued from
    # separate threads genuinely interleave there, each opening its own DB
    # session/connection -- unlike calling the app from a single thread,
    # which would just serialize the two requests at the Python level and
    # never actually race for SQLite's file lock at all.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_post_trigger, rid) for rid in rivulet_ids]
        responses = [f.result(timeout=10) for f in futures]

    for response in responses:
        assert response.status_code == 201, response.text

    for rivulet_id in rivulet_ids:
        messages = disk_client.get(
            f"/api/v1/rivulets/{rivulet_id}/messages", headers=headers
        ).json()
        contents = [m["content"] for m in messages]
        assert "go do it" in contents
        assert "OK." in contents
