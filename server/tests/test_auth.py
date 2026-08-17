import asyncio
import sqlite3
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from rivulets.agentos.service import get_agentos, init_agentos, reset_agentos_for_testing
from rivulets.app import create_app
from rivulets.config import Settings, get_settings
from rivulets.db.session import get_engine, init_db, make_engine, override_engine
from rivulets.security import keys
from rivulets.security.rate_limit import (
    get_invite_accept_rate_limiter,
    get_login_rate_limiter,
    get_webhook_trigger_rate_limiter,
)
from rivulets.security.session import get_session_key_store
from rivulets.security.webhook_signing import get_webhook_replay_guard
from rivulets.sync import get_sync_engine
from rivulets.sync.engine import SyncEngine, reset_sync_engine_for_testing

_ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- exercising #318's gate, never actually bound
_TEST_BOOTSTRAP_TOKEN = "correct-token"  # noqa: S105 -- test fixture value, not a real secret


def _settings_bound_to(
    host: str,
    *,
    require_bootstrap_token: bool = False,
    bootstrap_token: str | None = None,
) -> Settings:
    # Same fields as get_settings() would produce, just with app_server_host
    # (and optionally the bootstrap-token gate) overridden -- workspace_dir/db
    # paths are irrelevant to login() and never touched by these tests.
    base = get_settings()
    return Settings(
        app_server_host=host,
        workspace_dir=base.workspace_dir,
        require_bootstrap_token=require_bootstrap_token,
        bootstrap_token=bootstrap_token,
    )


def test_login_bootstraps_workspace_on_first_use(client: TestClient) -> None:
    mnemonic = keys.generate_mnemonic()
    response = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_rejects_invalid_mnemonic(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"key": "not a real phrase"})
    assert response.status_code == 400


def test_second_login_requires_same_mnemonic(client: TestClient) -> None:
    first = keys.generate_mnemonic()
    ok = client.post("/api/v1/auth/login", json={"key": first})
    assert ok.status_code == 200

    other = keys.generate_mnemonic()
    rejected = client.post("/api/v1/auth/login", json={"key": other})
    assert rejected.status_code == 401


def test_bootstrap_with_require_flag_refuses_without_a_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#247/#318: with no workspace yet and RIVULETS_REQUIRE_BOOTSTRAP_TOKEN
    set, the first login must not be able to claim the workspace unless
    RIVULETS_BOOTSTRAP_TOKEN is configured."""
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(_ALL_INTERFACES, require_bootstrap_token=True),
    )

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 401
    assert "bootstrap" in response.json()["detail"].lower()


def test_bootstrap_with_require_flag_refuses_a_wrong_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(
            _ALL_INTERFACES, require_bootstrap_token=True, bootstrap_token=_TEST_BOOTSTRAP_TOKEN
        ),
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"key": keys.generate_mnemonic(), "bootstrap_token": "wrong-token"},
    )

    assert response.status_code == 401


def test_bootstrap_with_require_flag_succeeds_with_the_correct_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(
            _ALL_INTERFACES, require_bootstrap_token=True, bootstrap_token=_TEST_BOOTSTRAP_TOKEN
        ),
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"key": keys.generate_mnemonic(), "bootstrap_token": _TEST_BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 200, response.text
    assert response.json()["token"]


def test_bootstrap_with_require_flag_rejects_a_length_mismatched_token_with_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#291: hmac.compare_digest(supplied.encode(), configured.encode())
    must not blow up into a 500 just because the two buffers are
    different lengths -- an empty (or short) supplied token against a
    configured one is exactly as run-of-the-mill wrong as any other
    mismatch, and should fail closed the same documented 401 way."""
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(
            _ALL_INTERFACES, require_bootstrap_token=True, bootstrap_token=_TEST_BOOTSTRAP_TOKEN
        ),
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"key": keys.generate_mnemonic(), "bootstrap_token": ""},
    )

    assert response.status_code == 401
    assert "bootstrap" in response.json()["detail"].lower()


def test_second_login_with_require_flag_does_not_require_a_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the workspace already exists, the bootstrap-token gate no
    longer applies -- it only guards the moment the workspace row is
    created, not every subsequent login."""
    mnemonic = keys.generate_mnemonic()
    first = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert first.status_code == 200

    monkeypatch.setattr(
        "rivulets.api.auth.get_settings",
        lambda: _settings_bound_to(_ALL_INTERFACES, require_bootstrap_token=True),
    )
    second = client.post("/api/v1/auth/login", json={"key": mnemonic})

    assert second.status_code == 200, second.text


def test_bootstrap_over_loopback_does_not_require_a_bootstrap_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default bind (127.0.0.1), with the gate off by default, is only
    reachable from this machine already -- no token needed."""
    monkeypatch.setattr("rivulets.api.auth.get_settings", lambda: _settings_bound_to("127.0.0.1"))

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 200, response.text


def test_bootstrap_over_0_0_0_0_does_not_require_a_bootstrap_token_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318: the Docker image always binds 0.0.0.0 internally, including
    the documented default (loopback-published) compose/`docker run` path,
    which never sets RIVULETS_REQUIRE_BOOTSTRAP_TOKEN. That default must
    behave like a native loopback install -- first login succeeds with no
    token -- since app_server_host alone can't tell a loopback-only
    publish from a LAN one apart (see config.py's require_bootstrap_token
    docstring). Before this fix, gating on app_server_host=="0.0.0.0"
    directly meant this always 401'd."""
    monkeypatch.setattr(
        "rivulets.api.auth.get_settings", lambda: _settings_bound_to(_ALL_INTERFACES)
    )

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 200, response.text
    assert response.json()["token"]


def test_protected_endpoint_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/channels")
    assert response.status_code == 401


def test_protected_endpoint_accepts_valid_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/channels", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_login_is_rate_limited_after_five_attempts_per_minute(client: TestClient) -> None:
    """security-and-dr.md's documented "5 attempts per minute per IP"
    brute-force mitigation -- every attempt counts toward the cap
    regardless of outcome, so this also covers a flood of wrong guesses,
    not just repeated valid logins."""
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
        assert response.status_code in (200, 401)

    sixth = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    assert sixth.status_code == 429


def test_login_rate_limit_is_scoped_independently_per_ip(client: TestClient) -> None:
    """A direct unit check on the limiter itself (rather than TestClient,
    which always presents the same client IP) that two different IPs get
    independent budgets."""
    limiter = get_login_rate_limiter()
    for _ in range(5):
        assert limiter.check("1.2.3.4") is True
    assert limiter.check("1.2.3.4") is False
    assert limiter.check("5.6.7.8") is True


def test_login_succeeds_even_when_the_sync_engine_fails_to_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-9.5: a node must be fully functional with sync unreachable,
    including sync itself failing to come up at login time -- this must
    never turn into a failed login."""

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("libp2p host failed to bind")

    monkeypatch.setattr("rivulets.sync.engine.SyncEngine.start", _boom)

    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})

    assert response.status_code == 200, response.text
    assert response.json()["token"]


def test_login_seeds_starter_agents_and_team_on_first_workspace_creation(
    client: TestClient,
) -> None:
    """#16: the moment a workspace is bootstrapped (first-ever login on a
    fresh DB), the starter agent/team library should already be there."""
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    headers = {"Authorization": f"Bearer {response.json()['token']}"}

    agents = client.get("/api/v1/agents", headers=headers).json()
    assert {agent["name"] for agent in agents} == {"Assistant", "Coder", "Researcher", "Writer"}

    teams = client.get("/api/v1/teams", headers=headers).json()
    assert [team["name"] for team in teams] == ["Starter Team"]

    assistant_id = next(agent["id"] for agent in agents if agent["name"] == "Assistant")
    rules = client.get(f"/api/v1/agents/{assistant_id}/routing-rules", headers=headers).json()
    assert [rule["rule_type"] for rule in rules] == ["always"]

    writer_id = next(agent["id"] for agent in agents if agent["name"] == "Writer")
    writer_rules = client.get(f"/api/v1/agents/{writer_id}/routing-rules", headers=headers).json()
    assert writer_rules[0]["rule_type"] == "keyword"
    assert "draft" in writer_rules[0]["pattern"]


def test_login_reregisters_after_restart_with_locked_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#404: closer to a Docker restart than the in-memory-keyring
    simulation above. Provider keys live only in the encrypted fallback;
    a process restart clears the session key and the AgentOS list.
    Login must unlock the store and leave every starter agent runnable.
    """
    import keyring
    import keyring.errors

    def _no_keyring(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", _no_keyring)
    monkeypatch.setattr(keyring, "set_password", _no_keyring)
    monkeypatch.setattr(keyring, "delete_password", _no_keyring)

    mnemonic = keys.generate_mnemonic()
    first = client.post("/api/v1/auth/login", json={"key": mnemonic})
    headers = {"Authorization": f"Bearer {first.json()['token']}"}

    added = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-test"},
        headers=headers,
    )
    assert added.status_code == 201, added.text

    get_agentos().agents = []
    get_session_key_store().clear()

    second = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert second.status_code == 200, second.text

    registered = [a for a in (get_agentos().agents or []) if getattr(a, "model", None) is not None]
    listed = client.get("/api/v1/agents", headers=headers).json()
    assert {agent["id"] for agent in listed} <= {a.id for a in registered}
    assert all(agent["agentos_agent_id"] == agent["id"] for agent in listed)
    assert len(registered) >= 4


def test_login_reregisters_agents_after_session_unlocks_credentials(client: TestClient) -> None:
    """Startup sync_agents() cannot resolve provider keys stored in the
    encrypted fallback (Docker / no OS keychain) because the credential
    store is locked until login. After a restart the in-process AgentOS
    registry is empty; the next login must rebuild it or dispatch will
    match agents from the DB and then silently fail with 'not registered'.
    """
    mnemonic = keys.generate_mnemonic()
    first = client.post("/api/v1/auth/login", json={"key": mnemonic})
    headers = {"Authorization": f"Bearer {first.json()['token']}"}

    added = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-test"},
        headers=headers,
    )
    assert added.status_code == 201, added.text

    # Simulate a process restart: AgentOS is in-memory and empty again,
    # even though the DB still has the starter roster + a usable provider.
    get_agentos().agents = []

    second = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert second.status_code == 200, second.text

    registered_ids = {agent.id for agent in (get_agentos().agents or [])}
    listed = client.get("/api/v1/agents", headers=headers).json()
    assert {agent["id"] for agent in listed} <= registered_ids
    assert len(registered_ids) >= 4


def test_second_login_does_not_reseed_starter_content(client: TestClient) -> None:
    mnemonic = keys.generate_mnemonic()
    first = client.post("/api/v1/auth/login", json={"key": mnemonic})
    headers = {"Authorization": f"Bearer {first.json()['token']}"}

    second = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert second.status_code == 200

    agents = client.get("/api/v1/agents", headers=headers).json()
    assert len(agents) == 4
    teams = client.get("/api/v1/teams", headers=headers).json()
    assert len(teams) == 1


def test_logout_clears_the_session_and_stops_the_sync_engine(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/auth/logout", headers=auth_headers)
    assert response.status_code == 204

    with pytest.raises(RuntimeError, match="No active session"):
        get_session_key_store().get_key()

    # The session is gone, so the same bearer token is no longer accepted.
    assert client.get("/api/v1/channels", headers=auth_headers).status_code == 401


def test_unauthenticated_logout_does_not_clear_a_logged_in_workspaces_session(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#228: an unauthenticated POST /auth/logout used to be able to wipe
    the process-wide SessionKeyStore (JWT signing key, P2P PSK,
    credential-store key, webhook-secret key) and stop sync for every
    session on the node -- from a plain cross-site form POST, no bearer
    token required. It must now be a no-op unless the caller presents the
    workspace's own valid session token."""
    no_token = client.post("/api/v1/auth/logout")
    assert no_token.status_code == 204

    bad_token = client.post(
        "/api/v1/auth/logout", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert bad_token.status_code == 204

    # The already-logged-in workspace's session must still be intact.
    assert client.get("/api/v1/channels", headers=auth_headers).status_code == 200


def test_invite_grant_logout_does_not_clear_the_owners_session(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#284: an invite-redeemed session (#15, grant="invite") passing
    _decode_session_token successfully used to be enough to reach the same
    process-wide teardown #228 gated against unauthenticated callers --
    letting an invited guest's Sign out button wipe the JWT signing key,
    P2P PSK, credential-store key, and webhook-secret key, and stop sync,
    for the whole node. Only grant="owner" may do that; an invite-grant
    logout must be a silent 204 no-op, same as no token at all."""
    stop_calls = 0

    async def _count_stop(*_args: object, **_kwargs: object) -> None:
        nonlocal stop_calls
        stop_calls += 1

    # The `client` fixture already no-ops SyncEngine.stop (conftest.py) so
    # the suite never touches real sockets -- swapped here for a call
    # counter instead, since `.running` never flips True under that no-op
    # either way and can't be used as a stand-in for "was stop() reached".
    monkeypatch.setattr(get_sync_engine(), "stop", _count_stop)

    created = client.post("/api/v1/invites", json={}, headers=auth_headers).json()
    accepted = client.post(
        "/api/v1/invites/accept",
        json={"invite_token": created["url"].rsplit("/", 1)[-1], "display_name": "Guest One"},
    ).json()
    invite_headers = {"Authorization": f"Bearer {accepted['token']}"}

    response = client.post("/api/v1/auth/logout", headers=invite_headers)
    assert response.status_code == 204
    assert stop_calls == 0

    # The owner's own session must still be intact.
    assert client.get("/api/v1/channels", headers=auth_headers).status_code == 200
    assert get_session_key_store().get_key()


def test_login_derives_a_credential_store_key(client: TestClient) -> None:
    """#118: login always derives the encrypted-SQLite fallback's
    encryption key alongside the JWT signing key and P2P PSK, even though
    most nodes never end up reading it (session.py's module docstring) —
    it's only used when the OS keychain has no usable backend."""
    response = client.post("/api/v1/auth/login", json={"key": keys.generate_mnemonic()})
    assert response.status_code == 200

    assert get_session_key_store().get_credential_store_key()


def test_stream_ticket_requires_a_valid_session(client: TestClient) -> None:
    response = client.post("/api/v1/auth/stream-ticket")
    assert response.status_code == 401


def test_stream_ticket_is_a_short_lived_purpose_scoped_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The one thing the SSE route's query-param auth path (api/deps.py's
    get_current_workspace_id_for_stream) accepts -- a normal session token
    passed the same way is rejected, closing off the long-lived-token-in-a-
    URL leak this ticket exists to replace."""
    response = client.post("/api/v1/auth/stream-ticket", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ticket"]
    assert body["expires_at"]

    payload = pyjwt.decode(body["ticket"], options={"verify_signature": False})
    assert payload["purpose"] == "stream"

    # Expires in roughly a minute, not the ~24h a normal session token gets.
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at - datetime.now(UTC) < timedelta(minutes=2)


def test_stream_ticket_cannot_be_used_as_a_bearer_session_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#234: `purpose == "stream"` is supposed to confine a minted ticket to
    the one query-param auth path it was designed for (get_current_
    workspace_id_for_stream) -- before this fix, _decode_token / get_
    session_claims never inspected `purpose`, so presenting the ticket as a
    normal `Authorization: Bearer` header made it a full, if short-lived,
    session on every other route."""
    ticket_response = client.post("/api/v1/auth/stream-ticket", headers=auth_headers)
    assert ticket_response.status_code == 200
    ticket = ticket_response.json()["ticket"]

    response = client.get("/api/v1/channels", headers={"Authorization": f"Bearer {ticket}"})
    assert response.status_code == 401


async def _noop_async(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
async def disk_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[TestClient]:
    """Same shape as conftest.py's `client` fixture, but wired to a real
    on-disk SQLite file instead of the in-memory/StaticPool engine that
    fixture hardcodes -- StaticPool hands every session in the process the
    *same* literal sqlite3 connection, so two concurrent requests never
    actually contend for SQLite's file lock there (see
    test_concurrent_write_lock.py's module docstring, which needs this same
    real-file setup for the identical reason)."""
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


def test_two_overlapping_first_logins_produce_one_workspace_row(
    disk_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#324: two concurrent first logins against an empty DB (two browser
    tabs on a fresh install, or two clients racing an unauthenticated
    bootstrap window) used to both see no `Workspace` row via
    select(Workspace).scalar_one_or_none() and both insert one -- bricking
    every later call to that same pattern (this route, and
    api/invites.py's accept_invite) with an unhandled MultipleResultsFound.
    login()'s begin_immediate serializes the two now, and Workspace.
    singleton's UNIQUE constraint (db/models.py) is the backstop if it ever
    races anyway -- either way, exactly one workspace row should exist
    afterward, and neither request should ever see a raw 500.

    Without an artificial delay, one coroutine's whole select-then-insert
    (including the synchronous bcrypt hash in between, which blocks the
    single shared event loop until it finishes) sails through to
    completion before the other's `select(Workspace)` even runs, so the
    two requests never land on the same `None` read to prove anything --
    same reasoning as test_concurrent_write_lock.py's mocked LLM delay.
    Delaying every commit (rather than the read) is what reliably forces
    this: it stalls the winner's `await db.commit()` open long enough for
    the loop to hand control to the second request's own read, which lands
    on `None` too before the winner's delayed commit finally lands.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    _original_commit = AsyncSession.commit

    async def _slow_commit(self: AsyncSession) -> object:
        await asyncio.sleep(0.05)
        return await _original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", _slow_commit)

    # Same mnemonic from both threads -- two tabs of the same fresh install
    # entering the one recovery phrase the human was just given, not two
    # different workspaces racing each other.
    mnemonic = keys.generate_mnemonic()

    def _post_login() -> httpx.Response:
        return disk_client.post("/api/v1/auth/login", json={"key": mnemonic})

    # Two real, concurrently-overlapping requests against the same on-disk
    # database file -- see test_concurrent_write_lock.py's identical
    # ThreadPoolExecutor pattern and its comment on why this needs real
    # separate threads rather than sequential calls from one.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_post_login) for _ in range(2)]
        responses = [f.result(timeout=10) for f in futures]

    for response in responses:
        assert response.status_code in (200, 409), response.text
    assert sum(1 for r in responses if r.status_code == 200) >= 1

    db_path = tmp_path / "workspace" / "rivulets.db"
    conn = sqlite3.connect(db_path)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM workspace").fetchone()[0]
    finally:
        conn.close()
    assert row_count == 1
