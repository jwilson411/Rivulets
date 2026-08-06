"""LLM provider configuration CRUD (api/providers.py, FR-1.4/1.5).

No prior test file exercised this router directly (test_rule_generation.py
uses POST /providers once, incidentally, to set up an agent-creation test).
CredentialStoreError paths are exercised by monkeypatching
rivulets.api.providers.store_provider_key -- the in-memory keyring fixture
in conftest.py always succeeds, so there's no way to trigger a real
keychain failure in this test environment.
"""

import pytest
from fastapi.testclient import TestClient

from rivulets.security.credentials import CredentialStoreError
from rivulets.security.credentials import store_provider_key as real_store_provider_key


def test_list_providers_starts_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/providers", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_add_provider_success_never_returns_raw_key(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/providers",
        json={
            "provider": "openai",
            "label": "OpenAI",
            "api_key": "sk-super-secret",
            "base_url": "https://api.openai.com/v1",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "openai"
    assert body["label"] == "OpenAI"
    assert body["base_url"] == "https://api.openai.com/v1"
    assert body["is_default"] is False
    assert "api_key" not in body
    assert "sk-super-secret" not in response.text

    listed = client.get("/api/v1/providers", headers=auth_headers)
    assert len(listed.json()) == 1


def test_add_provider_credential_store_failure_returns_500(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> str:
        raise CredentialStoreError("no keychain backend available")

    monkeypatch.setattr("rivulets.api.providers.store_provider_key", _boom)

    response = client.post(
        "/api/v1/providers",
        json={"provider": "anthropic", "label": "Anthropic", "api_key": "sk-ant"},
        headers=auth_headers,
    )
    assert response.status_code == 500
    assert "no keychain backend available" in response.text

    listed = client.get("/api/v1/providers", headers=auth_headers)
    assert listed.json() == []


def test_update_provider_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.patch(
        "/api/v1/providers/nonexistent", json={"label": "New label"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_update_provider_label_and_base_url(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/providers",
        json={"provider": "deepseek", "label": "DeepSeek", "api_key": "sk-ds"},
        headers=auth_headers,
    )
    provider_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/providers/{provider_id}",
        json={"label": "DeepSeek Prod", "base_url": "https://custom.example/v1"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["label"] == "DeepSeek Prod"
    assert body["base_url"] == "https://custom.example/v1"


def test_update_provider_rotates_key(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-old"},
        headers=auth_headers,
    )
    provider_id = created.json()["id"]

    calls: list[tuple[str, str]] = []

    def _tracking_store(provider_config_id: str, api_key: str) -> str:
        calls.append((provider_config_id, api_key))
        return real_store_provider_key(provider_config_id, api_key)

    monkeypatch.setattr("rivulets.api.providers.store_provider_key", _tracking_store)

    updated = client.patch(
        f"/api/v1/providers/{provider_id}",
        json={"api_key": "sk-new"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert calls == [(provider_id, "sk-new")]


def test_update_provider_key_rotation_credential_store_failure(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-old"},
        headers=auth_headers,
    )
    provider_id = created.json()["id"]

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise CredentialStoreError("keychain write failed")

    monkeypatch.setattr("rivulets.api.providers.store_provider_key", _boom)

    updated = client.patch(
        f"/api/v1/providers/{provider_id}",
        json={"api_key": "sk-new"},
        headers=auth_headers,
    )
    assert updated.status_code == 500


def test_remove_provider_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.delete("/api/v1/providers/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_remove_provider_success(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-x"},
        headers=auth_headers,
    )
    provider_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/providers/{provider_id}", headers=auth_headers)
    assert deleted.status_code == 204

    listed = client.get("/api/v1/providers", headers=auth_headers)
    assert listed.json() == []


def test_remove_provider_in_use_by_agent_is_blocked(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider backing a live agent can't be pulled out from under it --
    matches remove_provider's 409 CONFLICT branch. _run_generator is
    monkeypatched the same way test_rule_generation.py does, so creating
    the agent below doesn't make a real LLM call."""

    async def fake_run_generator(*_args: object, **_kwargs: object) -> object:
        from rivulets.dispatch.rule_generation import GeneratedRoutingRules

        return GeneratedRoutingRules(rules=[])

    monkeypatch.setattr("rivulets.dispatch.rule_generation._run_generator", fake_run_generator)

    created_provider = client.post(
        "/api/v1/providers",
        json={"provider": "openai", "label": "OpenAI", "api_key": "sk-x"},
        headers=auth_headers,
    )
    provider_id = created_provider.json()["id"]

    created_agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Helper",
            "description": "A helper agent that does helpful things daily.",
            "instructions": "Be helpful.",
            "model": "openai:gpt-4o-mini",
        },
        headers=auth_headers,
    )
    assert created_agent.status_code == 201, created_agent.text

    response = client.delete(f"/api/v1/providers/{provider_id}", headers=auth_headers)
    assert response.status_code == 409
    assert "Helper" in response.text

    listed = client.get("/api/v1/providers", headers=auth_headers)
    assert len(listed.json()) == 1
