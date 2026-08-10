import os
import shutil
import tempfile
from collections.abc import AsyncIterator

# Must happen before the first `rivulets.config.get_settings()` call
# anywhere (it's @lru_cache'd — whatever env var is set on that first call
# sticks for the whole process). Without this, app startup's
# ensure_workspace_dirs() and AgentOS's SqliteDb would touch the real
# ~/.rivulets on the machine running the tests.
_TEST_WORKSPACE_DIR = tempfile.mkdtemp(prefix="rivulets-test-")
os.environ["RIVULETS_WORKSPACE_DIR"] = _TEST_WORKSPACE_DIR

import keyring  # noqa: E402
import keyring.errors  # noqa: E402
import pytest  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class _InMemoryKeyring(KeyringBackend):
    """No real OS keychain (macOS Keychain, SecretService, ...) is
    available on GitHub-hosted Linux runners, so `keyring.set_password`
    raises `NoKeyringError` there — see rivulets.security.credentials.
    Installing this backend for the whole test session keeps credential
    storage working identically in CI and on a developer's machine,
    without ever touching whatever real keychain the machine has."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self._store:
            raise keyring.errors.PasswordDeleteError("No such password.")
        del self._store[key]


keyring.set_keyring(_InMemoryKeyring())
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from rivulets.agentos.service import init_agentos, reset_agentos_for_testing  # noqa: E402
from rivulets.app import create_app  # noqa: E402
from rivulets.config import get_settings  # noqa: E402
from rivulets.db.session import (  # noqa: E402
    get_engine,
    init_db,
    make_engine,
    override_engine,
    session_scope,
)
from rivulets.security import keys  # noqa: E402
from rivulets.security.rate_limit import (  # noqa: E402
    get_invite_accept_rate_limiter,
    get_login_rate_limiter,
    get_webhook_trigger_rate_limiter,
)
from rivulets.security.session import get_session_key_store  # noqa: E402
from rivulets.sync.engine import (  # noqa: E402
    SyncEngine,
    init_sync_engine,
    reset_sync_engine_for_testing,
)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    shutil.rmtree(_TEST_WORKSPACE_DIR, ignore_errors=True)


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TestClient]:
    """A TestClient wired to a fresh in-memory SQLite DB per test — never
    touches the real ~/.rivulets workspace.

    SyncEngine.start()/.stop() are no-op'd here: login (api/auth.py) always
    tries to start the real sync engine, and the general test suite logs
    in constantly via the auth_headers fixture below. Actually spinning up
    a libp2p host (real sockets, real mDNS/zeroconf) on every one of those
    would make the suite slow and network-dependent for no benefit — the
    engine's real behavior is covered by tests/test_sync.py, which
    exercises real SyncEngine instances directly, not through this
    fixture. sync/apply.py's conflict-resolution logic doesn't touch the
    network at all and is tested here without patching."""
    monkeypatch.setattr(SyncEngine, "start", _noop_async)
    monkeypatch.setattr(SyncEngine, "stop", _noop_async)
    reset_sync_engine_for_testing()
    # Every test using this fixture spins up a fresh app, and lifespan()
    # would otherwise VACUUM INTO + integrity-check a backup snapshot on
    # each one — real behavior worth its own tests (test_backup.py), but
    # pure overhead here against an ephemeral in-memory DB nobody restores.
    monkeypatch.setattr("rivulets.app.run_startup_backup_checks", _noop_async)
    # #92's scheduler_task is started as a bare asyncio.create_task at
    # lifespan startup, with no ordering guarantee against the test's own
    # first request. Since the in-memory test DB uses StaticPool (all
    # sessions share one literal SQLite connection), the scheduler's own
    # session_scope() opening/closing on that shared connection can land
    # in the middle of an unrelated request's own commit()/refresh() pair
    # and invalidate it -- surfaced as an intermittent
    # `sqlalchemy.exc.InvalidRequestError: Could not refresh instance`
    # around login()'s Workspace bootstrap. The real scheduling behavior
    # is covered by tests/test_scheduler.py against `_tick()` directly,
    # not through this fixture, so no-op it here the same way
    # SyncEngine.start/.stop are above.
    monkeypatch.setattr("rivulets.app.run_scheduler_loop", _noop_async)
    # #96's run_retention_loop mirrors run_scheduler_loop exactly (its own
    # bare asyncio.create_task at lifespan startup, its own session_scope()
    # per tick against the same shared StaticPool connection) and races the
    # test suite the same way -- no-op it here too. Real retention behavior
    # is covered by tests/test_tracing.py against prune_old_traces directly.
    monkeypatch.setattr("rivulets.app.run_retention_loop", _noop_async)

    override_engine(make_engine(in_memory=True))
    reset_agentos_for_testing()
    # The login rate limiter (security/rate_limit.py) is a module-level
    # singleton, not per-app state — without resetting it here, tests
    # sharing TestClient's fixed client IP would trip each other's 5/min
    # cap well before the suite finishes.
    get_login_rate_limiter().reset_for_testing()
    get_invite_accept_rate_limiter().reset_for_testing()
    get_webhook_trigger_rate_limiter().reset_for_testing()

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_session_key_store().clear()
    # Dispose before dropping the reference — aiosqlite runs each
    # connection's work on its own background thread that calls back into
    # this test's event loop; without an explicit dispose() that thread
    # can still be finishing up after pytest-asyncio closes the loop for
    # the next test, surfacing as a flaky "Event loop is closed"
    # PytestUnhandledThreadExceptionWarning attributed to an unrelated,
    # later test.
    await get_engine().dispose()
    override_engine(None)
    reset_agentos_for_testing()
    reset_sync_engine_for_testing()
    get_login_rate_limiter().reset_for_testing()
    get_invite_accept_rate_limiter().reset_for_testing()
    get_webhook_trigger_rate_limiter().reset_for_testing()


async def _noop_async(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A bare in-memory-DB session, for tests that need DB access without
    the HTTP/app layer (e.g. exercising agentos/models.py directly).

    Also resets/initializes the AgentOS singleton (same as the `client`
    fixture): sync/apply.py's handle_incoming_state_change calls
    sync_agents() after applying a remote "agent" change (issue #10 --
    without it, a node that only ever *receives* an Agent row via sync has
    no matching in-process AgentOS registration), which needs
    get_agentos() to not raise "not initialized".

    Also resets/initializes the sync engine singleton: unlike the `client`
    fixture (whose TestClient(app) triggers create_app()'s own
    init_sync_engine() call), nothing else does that for a bare DB
    session, so any code path exercised here that calls
    sync/publish.py's publish_current_state (workflows/engine.py's
    self-contained per-message publish, #24) would otherwise hit
    get_sync_engine()'s "not initialized" RuntimeError -- and whether it
    actually does depends on test *execution order* (a prior client-based
    test's teardown may have already reset the process-wide singleton to
    None), which is exactly the kind of order-dependent flake this
    fixture should not leave to chance. Never started (`.start()` is
    never called, matching client's own monkeypatch-to-noop), so
    `.running` stays False and publish_current_state degrades to
    recording a SyncPendingOutbound row, same as an offline node."""
    override_engine(make_engine(in_memory=True))
    await init_db()
    reset_agentos_for_testing()
    init_agentos()
    reset_sync_engine_for_testing()
    init_sync_engine(get_settings().sync_dir)
    async with session_scope() as session:
        yield session
    await get_engine().dispose()  # see client fixture's comment on why
    override_engine(None)
    reset_agentos_for_testing()
    reset_sync_engine_for_testing()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Logs in with a fresh mnemonic, which bootstraps the workspace row on
    this in-memory DB (see api/auth.py), then claims a Human identity
    (#14) so the returned token carries a human_id -- most routes that
    write a Message now require CurrentHumanId, and the large existing
    body of tests posting via this fixture would otherwise 401."""
    mnemonic = keys.generate_mnemonic()
    response = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert response.status_code == 200, response.text
    token: str = response.json()["token"]
    identity_response = client.post(
        "/api/v1/auth/identity",
        json={"display_name": "Test User"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert identity_response.status_code == 200, identity_response.text
    token = identity_response.json()["token"]
    return {"Authorization": f"Bearer {token}"}
