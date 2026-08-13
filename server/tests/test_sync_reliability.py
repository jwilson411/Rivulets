"""P2P sync reliability hardening (FR-9.5) — split out from test_sync.py
since it's a distinct concern (retry/recovery mechanisms) from core sync
mechanics, and test_sync.py was already large.

Covers two retry queues:
  - Outbound: publish_entity_change queues an entity (SyncPendingOutbound)
    instead of just dropping the change when the sync engine isn't
    running or a publish attempt fails, and drain_pending_outbound
    (called on engine start / peer connect, api/auth.py + app.py) retries
    everything queued.
  - Inbound: apply_remote_change queues a full incoming message
    (SyncPendingInbound) instead of dropping it when it references an
    entity that hasn't synced here yet (Rivulet.channel_id/Message.rivulet_id's
    FK-ordering hazard, see sync/apply.py's module docstring), and
    retry_pending_inbound (called after every successful apply) retries
    everything queued, on the chance the missing dependency just arrived.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Agent, Channel, Rivulet, SyncPendingInbound, SyncPendingOutbound
from rivulets.sync.apply import (
    CHANNEL_SPEC,
    RIVULET_SPEC,
    TOMBSTONE_FIELD,
    apply_remote_change,
    record_local_change,
    retry_pending_inbound,
)
from rivulets.sync.engine import init_sync_engine, reset_sync_engine_for_testing
from rivulets.sync.publish import (
    build_entity_payload,
    drain_pending_outbound,
    publish_current_state,
    publish_entity_change,
    publish_tombstone,
)

_AGENT_FIELDS = {
    "description": "A test agent for reliability tests.",
    "instructions": "Do the thing.",
    "model": "openai:gpt-4",
}


@pytest.fixture
def not_running_sync_engine(tmp_path: Path) -> Iterator[None]:
    reset_sync_engine_for_testing()
    init_sync_engine(tmp_path / "sync")
    yield
    reset_sync_engine_for_testing()


class _FakeEngine:
    def __init__(self, *, running: bool, node_id: str = "node-a", fail: bool = False) -> None:
        self.running = running
        self.node_id = node_id
        self.fail = fail
        self.published: list[tuple[str, str, dict[str, object], dict[str, int]]] = []

    async def publish_state_change(
        self,
        entity_type: str,
        entity_id: str,
        payload: dict[str, object],
        vector_clock: dict[str, int],
    ) -> None:
        if self.fail:
            raise RuntimeError("simulated publish failure")
        self.published.append((entity_type, entity_id, payload, vector_clock))


async def test_publish_entity_change_queues_when_engine_not_running(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    await publish_entity_change(db_session, "agent", "agent-1", {"name": "x"})

    pending = await db_session.get(SyncPendingOutbound, ("agent", "agent-1"))
    assert pending is not None


async def test_publish_entity_change_queues_when_publish_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine(running=True, fail=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await publish_entity_change(db_session, "agent", "agent-1", {"name": "x"})

    pending = await db_session.get(SyncPendingOutbound, ("agent", "agent-1"))
    assert pending is not None


async def test_publish_entity_change_does_not_queue_on_success(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine(running=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await publish_entity_change(db_session, "agent", "agent-1", {"name": "x"})

    assert await db_session.get(SyncPendingOutbound, ("agent", "agent-1")) is None
    assert fake.published == [("agent", "agent-1", {"name": "x"}, {"node-a": 1})]


async def test_build_entity_payload_reconstructs_current_state(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-1", name="Rebuilt", **_AGENT_FIELDS))
    await db_session.commit()

    payload = await build_entity_payload(db_session, "agent", "agent-1")
    assert payload == {
        "name": "Rebuilt",
        "description": _AGENT_FIELDS["description"],
        "instructions": _AGENT_FIELDS["instructions"],
        "model": _AGENT_FIELDS["model"],
        "fallback_models": None,
        "output_schema": None,
    }


async def test_build_entity_payload_returns_none_for_deleted_entity(
    db_session: AsyncSession,
) -> None:
    assert await build_entity_payload(db_session, "agent", "does-not-exist") is None


async def test_publish_current_state_is_noop_for_deleted_entity(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine(running=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await publish_current_state(db_session, "agent", "does-not-exist")

    assert fake.published == []


async def test_publish_tombstone_queues_when_engine_not_running(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    await publish_tombstone(db_session, "agent", "agent-1")

    pending = await db_session.get(SyncPendingOutbound, ("agent", "agent-1"))
    assert pending is not None
    assert pending.deleted is True


async def test_publish_tombstone_queues_when_publish_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine(running=True, fail=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await publish_tombstone(db_session, "agent", "agent-1")

    pending = await db_session.get(SyncPendingOutbound, ("agent", "agent-1"))
    assert pending is not None
    assert pending.deleted is True


async def test_publish_tombstone_sends_deleted_marker_on_success(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine(running=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await publish_tombstone(db_session, "agent", "agent-1")

    assert await db_session.get(SyncPendingOutbound, ("agent", "agent-1")) is None
    assert fake.published == [("agent", "agent-1", {TOMBSTONE_FIELD: True}, {"node-a": 1})]


async def test_record_pending_outbound_promotes_queued_edit_to_deleted(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    """The entity was updated (queued a live-state retry), then deleted
    before that retry ever ran (#238) -- the existing row must be promoted
    to deleted=True in place, not left as a live-state retry that would
    silently no-op (publish_current_state finds nothing to rebuild) and
    never tell any peer about the delete at all."""
    await publish_entity_change(db_session, "agent", "agent-1", {"name": "x"})
    pending = await db_session.get(SyncPendingOutbound, ("agent", "agent-1"))
    assert pending is not None
    assert pending.deleted is False

    await publish_tombstone(db_session, "agent", "agent-1")

    await db_session.refresh(pending)
    assert pending.deleted is True
    # Still exactly one row for this entity, not a second queued alongside it.
    all_pending = list((await db_session.execute(select(SyncPendingOutbound))).scalars().all())
    assert len(all_pending) == 1


async def test_drain_pending_outbound_republishes_and_clears_queue(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Channel(id="chan-1", name="queued-channel"))
    db_session.add(SyncPendingOutbound(entity_type="channel", entity_id="chan-1"))
    await db_session.commit()

    fake = _FakeEngine(running=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await drain_pending_outbound(db_session)

    assert len(fake.published) == 1
    entity_type, entity_id, payload, _ = fake.published[0]
    assert entity_type == "channel"
    assert entity_id == "chan-1"
    assert payload["name"] == "queued-channel"
    assert await db_session.get(SyncPendingOutbound, ("channel", "chan-1")) is None


async def test_drain_pending_outbound_drops_queue_entry_for_deleted_entity(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entity was deleted before the retry ran -- nothing to publish,
    but the queue entry must still be cleared, not retried forever."""
    db_session.add(SyncPendingOutbound(entity_type="channel", entity_id="never-existed"))
    await db_session.commit()

    fake = _FakeEngine(running=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await drain_pending_outbound(db_session)

    assert fake.published == []
    assert await db_session.get(SyncPendingOutbound, ("channel", "never-existed")) is None


async def test_drain_pending_outbound_republishes_tombstone_for_deleted_entity(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#238: a queued tombstone (deleted=True, from a delete that happened
    while the engine was down or a publish failed) must retry via
    publish_tombstone, not publish_current_state -- the entity is already
    gone locally, so publish_current_state would just find nothing to
    rebuild and silently drop the delete instead of ever telling a peer."""
    db_session.add(SyncPendingOutbound(entity_type="agent", entity_id="agent-1", deleted=True))
    await db_session.commit()

    fake = _FakeEngine(running=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await drain_pending_outbound(db_session)

    assert fake.published == [("agent", "agent-1", {TOMBSTONE_FIELD: True}, {"node-a": 1})]
    assert await db_session.get(SyncPendingOutbound, ("agent", "agent-1")) is None


async def test_drain_pending_outbound_requeues_on_repeated_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Channel(id="chan-1", name="still-failing"))
    db_session.add(SyncPendingOutbound(entity_type="channel", entity_id="chan-1"))
    await db_session.commit()

    fake = _FakeEngine(running=True, fail=True)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    await drain_pending_outbound(db_session)

    # Still queued -- the row was deleted-then-requeued by
    # publish_entity_change's own failure path, not left dangling.
    assert await db_session.get(SyncPendingOutbound, ("channel", "chan-1")) is not None


async def test_drain_pending_outbound_noop_when_engine_not_running(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    db_session.add(Channel(id="chan-1", name="queued-channel"))
    db_session.add(SyncPendingOutbound(entity_type="channel", entity_id="chan-1"))
    await db_session.commit()

    await drain_pending_outbound(db_session)

    # Nothing drained -- still queued for the next time the engine starts.
    assert await db_session.get(SyncPendingOutbound, ("channel", "chan-1")) is not None


async def test_full_offline_then_recover_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end at the function level: create an agent while the engine
    isn't running (queued), then simulate the engine coming up and login's
    drain call -- the agent gets published exactly once, using its
    current state, and the queue ends up empty."""
    fake = _FakeEngine(running=False)
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: fake)

    db_session.add(Agent(id="agent-1", name="Created Offline", **_AGENT_FIELDS))
    await db_session.commit()
    await publish_entity_change(
        db_session,
        "agent",
        "agent-1",
        {"name": "Created Offline", **_AGENT_FIELDS},
    )
    assert await db_session.get(SyncPendingOutbound, ("agent", "agent-1")) is not None
    assert fake.published == []

    fake.running = True  # simulate the engine starting on next login
    await drain_pending_outbound(db_session)

    assert await db_session.get(SyncPendingOutbound, ("agent", "agent-1")) is None
    assert len(fake.published) == 1
    assert fake.published[0][0:2] == ("agent", "agent-1")


async def test_record_local_change_is_reused_by_generic_apply(db_session: AsyncSession) -> None:
    """Sanity check that record_local_change (used by both the immediate
    publish path and drain) still behaves as documented after the
    publish.py refactor -- unrelated regression risk, cheap to check."""
    vc = await record_local_change(db_session, "agent", "agent-1", "node-a")
    assert vc == {"node-a": 1}


async def test_apply_remote_change_queues_on_fk_ordering_failure(
    db_session: AsyncSession,
) -> None:
    """A rivulet referencing a channel that hasn't synced here yet must be
    queued (SyncPendingInbound), not silently lost forever."""
    result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "does-not-exist-yet",
            "title": "Hello",
            "status": "active",
            "created_by": "human",
        },
    )
    assert result.applied is False

    pending = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert len(pending) == 1
    assert pending[0].entity_type == "rivulet"
    assert pending[0].entity_id == "rivulet-1"
    assert pending[0].origin_node_id == "node-b"


async def test_retry_pending_inbound_applies_once_dependency_exists(
    db_session: AsyncSession,
) -> None:
    """The scenario the whole queue exists for: a rivulet arrives before
    its channel, gets queued; the channel arrives later (a normal
    successful apply); retrying the queue now succeeds."""
    result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {"channel_id": "chan-1", "title": "Hello", "status": "active", "created_by": "human"},
    )
    assert result.applied is False
    assert len(list((await db_session.execute(select(SyncPendingInbound))).scalars().all())) == 1

    # The channel "arrives" (a normal, unrelated apply).
    channel_result = await apply_remote_change(
        db_session,
        CHANNEL_SPEC,
        "chan-1",
        {"node-b": 1},
        "node-b",
        {"name": "general", "description": None, "position": 0, "archived": False},
    )
    assert channel_result.applied is True

    await retry_pending_inbound(db_session)

    assert await db_session.get(Rivulet, "rivulet-1") is not None
    assert (await db_session.execute(select(SyncPendingInbound))).scalars().all() == []


async def test_retry_pending_inbound_requeues_if_still_missing(db_session: AsyncSession) -> None:
    """Retrying before the dependency actually shows up must re-queue, not
    drop the message -- a delete-then-reattempt cycle without a
    correctness gap in either direction."""
    await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "still-does-not-exist",
            "title": "Hello",
            "status": "active",
            "created_by": "human",
        },
    )

    await retry_pending_inbound(db_session)

    pending = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert len(pending) == 1
    assert await db_session.get(Rivulet, "rivulet-1") is None
