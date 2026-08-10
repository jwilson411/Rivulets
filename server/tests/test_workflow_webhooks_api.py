"""#99: WorkflowWebhook CRUD (api/workflows.py) + the public HMAC-verified
trigger endpoint (api/webhooks.py) -- HTTP-level coverage, mirroring
test_workflow_schedules_api.py's CRUD style. See test_webhook_signing.py
for signature-verification logic in isolation.
"""

import time
from typing import Any

from fastapi.testclient import TestClient

from rivulets.security.webhook_signing import sign

_TIMESTAMP_HEADER = "X-Rivulets-Timestamp"
_SIGNATURE_HEADER = "X-Rivulets-Signature"


def _create_workflow(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/workflows", json={"name": name, "description": "test workflow"}, headers=headers
    )
    assert created.status_code == 201, created.text
    workflow_id: str = created.json()["id"]
    return workflow_id


def _create_channel(client: TestClient, headers: dict[str, str], name: str) -> str:
    channel = client.post("/api/v1/channels", json={"name": name}, headers=headers)
    assert channel.status_code == 201, channel.text
    channel_id: str = channel.json()["id"]
    return channel_id


def _add_transform_node(
    client: TestClient, headers: dict[str, str], workflow_id: str, template: str = "got: {input}"
) -> str:
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes",
        json={"name": "echo", "node_type": "transform", "config": {"template": template}},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    node_id: str = created.json()["id"]
    return node_id


def _connect_entry(client: TestClient, headers: dict[str, str], workflow_id: str, node_id: str) -> None:
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/connections",
        json={"from_node_id": None, "to_node_id": node_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


def _publish_workflow(client: TestClient, headers: dict[str, str], workflow_id: str) -> None:
    resp = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=headers)
    assert resp.status_code == 200, resp.text


def _get_run(client: TestClient, headers: dict[str, str], workflow_id: str, run_id: str) -> dict[str, Any]:
    resp = client.get(f"/api/v1/workflows/{workflow_id}/runs", headers=headers)
    assert resp.status_code == 200, resp.text
    runs = [r for r in resp.json() if r["id"] == run_id]
    assert len(runs) == 1
    return runs[0]


def _create_webhook(
    client: TestClient, headers: dict[str, str], workflow_id: str, channel_id: str, **extra: Any
) -> dict[str, Any]:
    body = {"channel_id": channel_id, **extra}
    resp = client.post(f"/api/v1/workflows/{workflow_id}/webhooks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _signed_headers(secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        _TIMESTAMP_HEADER: timestamp,
        _SIGNATURE_HEADER: sign(secret, timestamp, body),
    }


def _make_published_workflow_with_webhook(
    client: TestClient, headers: dict[str, str], name: str, **webhook_extra: Any
) -> dict[str, Any]:
    workflow_id = _create_workflow(client, headers, name)
    channel_id = _create_channel(client, headers, f"{name}-channel")
    node_id = _add_transform_node(client, headers, workflow_id)
    _connect_entry(client, headers, workflow_id, node_id)
    _publish_workflow(client, headers, workflow_id)
    webhook = _create_webhook(client, headers, workflow_id, channel_id, **webhook_extra)
    webhook["workflow_id"] = workflow_id
    webhook["channel_id"] = channel_id
    return webhook


def test_create_webhook_returns_secret_once_and_list_omits_it(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "orders")
    channel_id = _create_channel(client, auth_headers, "orders-channel")
    created = _create_webhook(client, auth_headers, workflow_id, channel_id, name="stripe")

    assert created["workflow_id"] == workflow_id
    assert created["channel_id"] == channel_id
    assert created["enabled"] is True
    assert created["last_triggered_at"] is None
    assert len(created["secret"]) > 20

    listed = client.get(f"/api/v1/workflows/{workflow_id}/webhooks", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert "secret" not in listed[0]


def test_create_webhook_does_not_require_published_workflow(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "draft-orders")
    channel_id = _create_channel(client, auth_headers, "draft-orders-channel")
    # Never published -- creation still succeeds; only firing is gated.
    _create_webhook(client, auth_headers, workflow_id, channel_id)


def test_create_webhook_rejects_unknown_channel(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "no-channel")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/webhooks",
        json={"channel_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_update_and_delete_webhook(client: TestClient, auth_headers: dict[str, str]) -> None:
    workflow_id = _create_workflow(client, auth_headers, "updatable")
    channel_id = _create_channel(client, auth_headers, "updatable-channel")
    created = _create_webhook(client, auth_headers, workflow_id, channel_id)

    updated = client.patch(
        f"/api/v1/workflows/{workflow_id}/webhooks/{created['id']}",
        json={"enabled": False, "name": "renamed"},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False
    assert updated.json()["name"] == "renamed"

    deleted = client.delete(
        f"/api/v1/workflows/{workflow_id}/webhooks/{created['id']}", headers=auth_headers
    )
    assert deleted.status_code == 204

    listed = client.get(f"/api/v1/workflows/{workflow_id}/webhooks", headers=auth_headers).json()
    assert listed == []


def test_rotate_secret_changes_secret_and_keeps_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "rotatable")
    channel_id = _create_channel(client, auth_headers, "rotatable-channel")
    created = _create_webhook(client, auth_headers, workflow_id, channel_id)

    rotated = client.post(
        f"/api/v1/workflows/{workflow_id}/webhooks/{created['id']}/rotate-secret",
        headers=auth_headers,
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["id"] == created["id"]
    assert rotated.json()["secret"] != created["secret"]

    # The old secret no longer verifies against the trigger endpoint.
    body = b'{"ping": true}'
    resp = client.post(
        f"/api/v1/webhooks/{created['id']}",
        content=body,
        headers=_signed_headers(created["secret"], body),
    )
    assert resp.status_code == 401


def test_trigger_with_valid_signature_fires_published_workflow(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    webhook = _make_published_workflow_with_webhook(client, auth_headers, "webhook-fires")
    body = b'{"event": "push"}'

    resp = client.post(
        f"/api/v1/webhooks/{webhook['id']}",
        content=body,
        headers=_signed_headers(webhook["secret"], body),
    )
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert payload["status"] == "completed"

    run = _get_run(client, auth_headers, webhook["workflow_id"], payload["run_id"])
    assert run["triggered_by"] == "webhook"
    assert run["triggered_by_id"] == webhook["id"]

    listed = client.get(
        f"/api/v1/workflows/{webhook['workflow_id']}/webhooks", headers=auth_headers
    ).json()
    assert listed[0]["last_triggered_at"] is not None


def test_trigger_applies_input_template_substitution(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Same "{input}" convention a transform node's config already uses
    (workflows/nodes.py's execute_transform_node) -- str.replace, not
    str.format, so a JSON body's own braces can't break rendering."""
    webhook = _make_published_workflow_with_webhook(
        client, auth_headers, "templated", input_template="webhook says: {input}"
    )
    body = b'{"count": 3}'
    resp = client.post(
        f"/api/v1/webhooks/{webhook['id']}",
        content=body,
        headers=_signed_headers(webhook["secret"], body),
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    run = _get_run(client, auth_headers, webhook["workflow_id"], run_id)
    assert run["final_output"] == 'got: webhook says: {"count": 3}'


def test_trigger_rejects_invalid_signature(client: TestClient, auth_headers: dict[str, str]) -> None:
    webhook = _make_published_workflow_with_webhook(client, auth_headers, "bad-sig")
    body = b"payload"
    headers = _signed_headers("wrong-secret", body)
    resp = client.post(f"/api/v1/webhooks/{webhook['id']}", content=body, headers=headers)
    assert resp.status_code == 401


def test_trigger_rejects_missing_signature_headers(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    webhook = _make_published_workflow_with_webhook(client, auth_headers, "no-sig")
    resp = client.post(f"/api/v1/webhooks/{webhook['id']}", content=b"payload")
    assert resp.status_code == 401


def test_trigger_rejects_expired_timestamp(client: TestClient, auth_headers: dict[str, str]) -> None:
    webhook = _make_published_workflow_with_webhook(client, auth_headers, "expired")
    body = b"payload"
    stale_timestamp = str(int(time.time()) - 600)
    headers = {
        _TIMESTAMP_HEADER: stale_timestamp,
        _SIGNATURE_HEADER: sign(webhook["secret"], stale_timestamp, body),
    }
    resp = client.post(f"/api/v1/webhooks/{webhook['id']}", content=body, headers=headers)
    assert resp.status_code == 401


def test_trigger_unknown_webhook_id_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/webhooks/does-not-exist",
        content=b"payload",
        headers={_TIMESTAMP_HEADER: str(int(time.time())), _SIGNATURE_HEADER: "irrelevant"},
    )
    assert resp.status_code == 404


def test_trigger_disabled_webhook_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    webhook = _make_published_workflow_with_webhook(client, auth_headers, "disabled-hook")
    client.patch(
        f"/api/v1/workflows/{webhook['workflow_id']}/webhooks/{webhook['id']}",
        json={"enabled": False},
        headers=auth_headers,
    )
    body = b"payload"
    resp = client.post(
        f"/api/v1/webhooks/{webhook['id']}", content=body, headers=_signed_headers(webhook["secret"], body)
    )
    assert resp.status_code == 404


def test_trigger_unpublished_workflow_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "still-draft")
    channel_id = _create_channel(client, auth_headers, "still-draft-channel")
    webhook = _create_webhook(client, auth_headers, workflow_id, channel_id)
    body = b"payload"
    resp = client.post(
        f"/api/v1/webhooks/{webhook['id']}", content=body, headers=_signed_headers(webhook["secret"], body)
    )
    assert resp.status_code == 409


def test_trigger_oversized_payload_returns_413(client: TestClient, auth_headers: dict[str, str]) -> None:
    webhook = _make_published_workflow_with_webhook(client, auth_headers, "too-big")
    body = b"x" * (262_144 + 1)
    resp = client.post(
        f"/api/v1/webhooks/{webhook['id']}", content=body, headers=_signed_headers(webhook["secret"], body)
    )
    assert resp.status_code == 413
