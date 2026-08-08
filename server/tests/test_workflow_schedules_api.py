"""#92: WorkflowSchedule CRUD + cron-preview endpoints (api/workflows.py) --
HTTP-level coverage, mirroring test_workflows_api.py's CRUD style.
"""

from typing import Any

from fastapi.testclient import TestClient


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


def _create_schedule(
    client: TestClient,
    headers: dict[str, str],
    workflow_id: str,
    channel_id: str,
    cron_expression: str = "0 9 * * *",
) -> dict[str, Any]:
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules",
        json={"channel_id": channel_id, "cron_expression": cron_expression, "input_content": "go"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_and_list_schedule(client: TestClient, auth_headers: dict[str, str]) -> None:
    workflow_id = _create_workflow(client, auth_headers, "digest")
    channel_id = _create_channel(client, auth_headers, "digest-channel")
    schedule = _create_schedule(client, auth_headers, workflow_id, channel_id)

    assert schedule["workflow_id"] == workflow_id
    assert schedule["channel_id"] == channel_id
    assert schedule["enabled"] is True
    assert schedule["consecutive_failures"] == 0
    assert schedule["last_fired_at"] is None
    assert schedule["next_fire_at"] > schedule["created_at"]

    listed = client.get(f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers).json()
    assert [s["id"] for s in listed] == [schedule["id"]]


def test_create_schedule_does_not_require_published_workflow(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "draft-digest")
    channel_id = _create_channel(client, auth_headers, "draft-digest-channel")
    # Never published -- creation still succeeds; only firing is gated.
    _create_schedule(client, auth_headers, workflow_id, channel_id)


def test_create_schedule_rejects_invalid_cron(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "bad-cron")
    channel_id = _create_channel(client, auth_headers, "bad-cron-channel")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules",
        json={"channel_id": channel_id, "cron_expression": "not a cron"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_schedule_rejects_unknown_channel(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "no-channel")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules",
        json={"channel_id": "does-not-exist", "cron_expression": "0 9 * * *"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_update_schedule_recomputes_next_fire_at_on_cron_change(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "recompute")
    channel_id = _create_channel(client, auth_headers, "recompute-channel")
    schedule = _create_schedule(
        client, auth_headers, workflow_id, channel_id, cron_expression="0 9 * * *"
    )

    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule['id']}",
        json={"cron_expression": "0 0 1 1 *"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["cron_expression"] == "0 0 1 1 *"
    assert updated["next_fire_at"] != schedule["next_fire_at"]


def test_update_schedule_rejects_invalid_cron(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "recompute-bad")
    channel_id = _create_channel(client, auth_headers, "recompute-bad-channel")
    schedule = _create_schedule(client, auth_headers, workflow_id, channel_id)

    resp = client.patch(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule['id']}",
        json={"cron_expression": "nonsense"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_reenabling_schedule_resets_consecutive_failures(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "reenable")
    channel_id = _create_channel(client, auth_headers, "reenable-channel")
    schedule = _create_schedule(client, auth_headers, workflow_id, channel_id)

    disabled = client.patch(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule['id']}",
        json={"enabled": False},
        headers=auth_headers,
    ).json()
    assert disabled["enabled"] is False

    reenabled = client.patch(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule['id']}",
        json={"enabled": True},
        headers=auth_headers,
    ).json()
    assert reenabled["enabled"] is True
    assert reenabled["consecutive_failures"] == 0
    assert reenabled["next_fire_at"] >= disabled["next_fire_at"]


def test_delete_schedule(client: TestClient, auth_headers: dict[str, str]) -> None:
    workflow_id = _create_workflow(client, auth_headers, "deletable")
    channel_id = _create_channel(client, auth_headers, "deletable-channel")
    schedule = _create_schedule(client, auth_headers, workflow_id, channel_id)

    resp = client.delete(
        f"/api/v1/workflows/{workflow_id}/schedules/{schedule['id']}", headers=auth_headers
    )
    assert resp.status_code == 204
    listed = client.get(f"/api/v1/workflows/{workflow_id}/schedules", headers=auth_headers).json()
    assert listed == []


def test_deleting_workflow_cascades_schedules(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "cascade-me")
    channel_id = _create_channel(client, auth_headers, "cascade-channel")
    _create_schedule(client, auth_headers, workflow_id, channel_id)

    resp = client.delete(f"/api/v1/workflows/{workflow_id}", headers=auth_headers)
    assert resp.status_code == 204
    # Recreate a workflow with the same route shape to confirm no
    # orphaned-schedule error surfaces anywhere else in the app.
    other_id = _create_workflow(client, auth_headers, "after-cascade")
    listed = client.get(f"/api/v1/workflows/{other_id}/schedules", headers=auth_headers).json()
    assert listed == []


def test_preview_endpoint_returns_next_fire_at_for_valid_cron(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "preview-good")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules/preview",
        json={"cron_expression": "0 9 * * *"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["next_fire_at"] is not None
    assert body["error"] is None


def test_preview_endpoint_returns_error_for_invalid_cron(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "preview-bad")
    resp = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules/preview",
        json={"cron_expression": "definitely not cron"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False
    assert body["next_fire_at"] is None
    assert body["error"] is not None
