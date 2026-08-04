from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_hive.app import create_app
from agent_hive.db.session import make_engine, override_engine
from agent_hive.security import keys
from agent_hive.security.session import get_session_key_store


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient wired to a fresh in-memory SQLite DB per test — never
    touches the real ~/.agent-hive workspace."""
    override_engine(make_engine(in_memory=True))

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_session_key_store().clear()
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
