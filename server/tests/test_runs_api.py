"""#414: cancel + lazy reap on GET /runs, and channel/rivulet filters
the thread cards use to show a latest-run status."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from rivulets.db.models import Channel, Rivulet, RunTrace
from rivulets.db.session import session_scope
from rivulets.tracing import start_trace


def _minutes_ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def test_cancel_running_run(client: TestClient, auth_headers: dict[str, str]) -> None:
    async with session_scope() as db:
        ctx = await start_trace(
            db, trigger_type="message", label="hello", rivulet_id=None, channel_id=None
        )
        await db.commit()
        trace_id = ctx.trace_id

    response = client.post(f"/api/v1/runs/{trace_id}/cancel", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["completed_at"] is not None

    listed = client.get("/api/v1/runs", headers=auth_headers).json()
    assert listed[0]["id"] == trace_id
    assert listed[0]["status"] == "cancelled"


async def test_cancel_completed_run_is_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    async with session_scope() as db:
        ctx = await start_trace(
            db, trigger_type="message", label="done", rivulet_id=None, channel_id=None
        )
        await db.commit()
        trace_id = ctx.trace_id
    assert client.post(f"/api/v1/runs/{trace_id}/cancel", headers=auth_headers).status_code == 200
    again = client.post(f"/api/v1/runs/{trace_id}/cancel", headers=auth_headers)
    assert again.status_code == 409


async def test_cancel_missing_run_is_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/runs/does-not-exist/cancel", headers=auth_headers)
    assert response.status_code == 404


async def test_list_runs_reaps_stale_zero_span(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    async with session_scope() as db:
        stale = RunTrace(
            trigger_type="message",
            label="How are you all doing today?",
            started_at=_minutes_ago(120),
        )
        db.add(stale)
        await db.commit()
        stale_id = stale.id

    listed = client.get("/api/v1/runs", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["id"] == stale_id
    assert listed[0]["status"] == "error"
    assert listed[0]["completed_at"] is not None


async def test_list_runs_filters_by_channel_and_rivulet(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    async with session_scope() as db:
        chan_a = Channel(name="filter-a")
        chan_b = Channel(name="filter-b")
        db.add_all([chan_a, chan_b])
        await db.flush()
        riv_a = Rivulet(channel_id=chan_a.id, created_by="human")
        riv_b = Rivulet(channel_id=chan_b.id, created_by="human")
        db.add_all([riv_a, riv_b])
        await db.flush()
        ctx_a = await start_trace(
            db, trigger_type="message", label="a", rivulet_id=riv_a.id, channel_id=chan_a.id
        )
        await start_trace(
            db, trigger_type="message", label="b", rivulet_id=riv_b.id, channel_id=chan_b.id
        )
        await db.commit()
        id_a = ctx_a.trace_id
        channel_a_id = chan_a.id
        rivulet_a_id = riv_a.id

    by_channel = client.get(f"/api/v1/runs?channel_id={channel_a_id}", headers=auth_headers).json()
    assert [t["id"] for t in by_channel] == [id_a]
    by_rivulet = client.get(f"/api/v1/runs?rivulet_id={rivulet_a_id}", headers=auth_headers).json()
    assert [t["id"] for t in by_rivulet] == [id_a]
