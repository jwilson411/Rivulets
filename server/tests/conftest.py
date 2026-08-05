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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from rivulets.agentos.service import reset_agentos_for_testing  # noqa: E402
from rivulets.app import create_app  # noqa: E402
from rivulets.db.session import (  # noqa: E402
    get_engine,
    init_db,
    make_engine,
    override_engine,
    session_scope,
)
from rivulets.security import keys  # noqa: E402
from rivulets.security.rate_limit import get_login_rate_limiter  # noqa: E402
from rivulets.security.session import get_session_key_store  # noqa: E402
from rivulets.sync.engine import SyncEngine, reset_sync_engine_for_testing  # noqa: E402


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

    override_engine(make_engine(in_memory=True))
    reset_agentos_for_testing()
    # The login rate limiter (security/rate_limit.py) is a module-level
    # singleton, not per-app state — without resetting it here, tests
    # sharing TestClient's fixed client IP would trip each other's 5/min
    # cap well before the suite finishes.
    get_login_rate_limiter().reset_for_testing()

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


async def _noop_async(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A bare in-memory-DB session, for tests that need DB access without
    the HTTP/app layer (e.g. exercising agentos/models.py directly)."""
    override_engine(make_engine(in_memory=True))
    await init_db()
    async with session_scope() as session:
        yield session
    await get_engine().dispose()  # see client fixture's comment on why
    override_engine(None)


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Logs in with a fresh mnemonic, which bootstraps the workspace row
    on this in-memory DB (see api/auth.py), and returns bearer headers."""
    mnemonic = keys.generate_mnemonic()
    response = client.post("/api/v1/auth/login", json={"key": mnemonic})
    assert response.status_code == 200, response.text
    token: str = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}
