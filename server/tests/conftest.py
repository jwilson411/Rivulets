import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator

# Must happen before the first `agent_hive.config.get_settings()` call
# anywhere (it's @lru_cache'd — whatever env var is set on that first call
# sticks for the whole process). Without this, app startup's
# ensure_workspace_dirs() and AgentOS's SqliteDb would touch the real
# ~/.agent-hive on the machine running the tests.
_TEST_WORKSPACE_DIR = tempfile.mkdtemp(prefix="agent-hive-test-")
os.environ["AGENT_HIVE_WORKSPACE_DIR"] = _TEST_WORKSPACE_DIR

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from agent_hive.agentos.service import reset_agentos_for_testing  # noqa: E402
from agent_hive.app import create_app  # noqa: E402
from agent_hive.db.session import init_db, make_engine, override_engine, session_scope  # noqa: E402
from agent_hive.security import keys  # noqa: E402
from agent_hive.security.session import get_session_key_store  # noqa: E402


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    shutil.rmtree(_TEST_WORKSPACE_DIR, ignore_errors=True)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient wired to a fresh in-memory SQLite DB per test — never
    touches the real ~/.agent-hive workspace."""
    override_engine(make_engine(in_memory=True))
    reset_agentos_for_testing()

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_session_key_store().clear()
    override_engine(None)
    reset_agentos_for_testing()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A bare in-memory-DB session, for tests that need DB access without
    the HTTP/app layer (e.g. exercising agentos/models.py directly)."""
    override_engine(make_engine(in_memory=True))
    await init_db()
    async with session_scope() as session:
        yield session
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
