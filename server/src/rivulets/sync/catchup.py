"""Snapshot-based catch-up sync (#347) — the DB/asyncio side.

state_snapshot.py documents the wire protocol and why it exists (gossip
only reaches peers present at publish time); this module builds the
snapshot itself and decides when to push it:

  - on every new peer connection (app.py's _on_peer_connected — fires on
    both sides of the connection, so a brand-new node both offers its
    empty state and receives everything the established node has), and
  - periodically (run_catchup_loop, started in app.py's lifespan), as
    anti-entropy for anything a live gossip publish dropped while
    connected.

The snapshot is reconstructed from VectorClockTracker rather than from
the entity tables directly, because the clock table is the one durable
record that covers deletes too: every publish (create, edit, or
tombstone) goes through record_local_change, and apply_remote_delete
deliberately never removes clock rows (sync/apply.py — that's what lets a
long-offline peer's stale edit still be judged LOCAL_NEWER instead of
resurrecting the entity). So a clock-tracked entity whose row still
exists snapshots as its current payload, and one whose row is gone
snapshots as a durable tombstone — exactly the replay #347 found missing:
"the tombstone is published once and not stored as a durable
deleted-clock" is no longer true, because the clock rows *are* the
durable deleted-clock and this module replays them."""

import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import SyncState, VectorClockTracker
from rivulets.db.session import session_scope
from rivulets.sync.apply import TOMBSTONE_FIELD, entity_pk_value, get_entity_spec
from rivulets.sync.engine import get_sync_engine
from rivulets.sync.publish import build_entity_payload
from rivulets.sync.state_snapshot import MAX_SNAPSHOT_FRAME_BYTES

logger = logging.getLogger(__name__)

# How often the periodic anti-entropy push re-offers this node's full
# state to every connected peer. Connect-time pushes cover the headline
# reconnect/join cases immediately; this interval only bounds how long a
# message *dropped by gossip while both peers were connected* can stay
# missing, so it doesn't need to be aggressive — and each push is cheap
# on the receiving side (an already-seen envelope is one clock SELECT
# judged EQUAL, then dropped).
CATCHUP_INTERVAL_SECONDS = 300


async def build_snapshot_envelopes(db: AsyncSession, own_node_id: str) -> list[bytes]:
    """Every synced entity this node has ever seen a clock for, as
    ready-to-send envelope frames: current payload for live entities,
    a tombstone for clock-tracked entities whose row is gone. Entities
    that exist but aren't publishable (e.g. a non-custom Tool — clock rows
    for those shouldn't exist, but a peer's data is not worth corrupting
    over "shouldn't") are skipped rather than mistaken for tombstones."""
    result = await db.execute(select(VectorClockTracker))
    clocks: dict[tuple[str, str], dict[str, int]] = {}
    for row in result.scalars():
        clocks.setdefault((row.entity_type, row.entity_id), {})[row.node_id] = row.clock

    envelopes: list[bytes] = []
    for (entity_type, entity_id), vector_clock in clocks.items():
        spec = get_entity_spec(entity_type)
        if spec is None:
            continue
        payload = await build_entity_payload(db, entity_type, entity_id)
        if payload is None:
            instance = await db.get(spec.model, entity_pk_value(spec, entity_id))
            if instance is not None:
                continue
            payload = {TOMBSTONE_FIELD: True}
        envelope = json.dumps(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "vector_clock": vector_clock,
                "origin_node_id": own_node_id,
                "payload": payload,
            }
        ).encode()
        if len(envelope) > MAX_SNAPSHOT_FRAME_BYTES:
            logger.warning(
                "Skipping oversized snapshot envelope for %s/%s (%d bytes)",
                entity_type,
                entity_id,
                len(envelope),
            )
            continue
        envelopes.append(envelope)
    return envelopes


async def push_snapshot_to_peer(
    db: AsyncSession, peer_id: str, envelopes: list[bytes] | None = None
) -> bool:
    """Offers this node's full synced state to one peer (state_snapshot.py's
    wire protocol). Best-effort like every other publish path: a failure is
    logged inside the engine and retried by the next periodic push, never
    raised. On success, records the sync time on the peer's SyncState row."""
    engine = get_sync_engine()
    if not engine.running:
        return False
    if envelopes is None:
        envelopes = await build_snapshot_envelopes(db, engine.node_id)
    if envelopes and not await engine.send_state_snapshot(peer_id, envelopes):
        return False
    now = utcnow_iso()
    state = await db.get(SyncState, peer_id)
    if state is None:
        db.add(SyncState(peer_node_id=peer_id, last_seen_at=now, last_sync_at=now))
    else:
        state.last_seen_at = now
        state.last_sync_at = now
    await db.commit()
    logger.info("Pushed state snapshot (%d envelopes) to peer %s", len(envelopes), peer_id)
    return True


async def push_snapshot_to_all_peers(db: AsyncSession) -> None:
    engine = get_sync_engine()
    if not engine.running:
        return
    peers = await engine.list_peers()
    if not peers:
        return
    envelopes = await build_snapshot_envelopes(db, engine.node_id)
    for peer in peers:
        await push_snapshot_to_peer(db, peer.peer_id, envelopes)


async def run_catchup_loop() -> None:
    """Runs for the app's lifetime (app.py's lifespan), same shape as
    workflows/scheduler.py's run_scheduler_loop: one tick's exception is
    logged and never kills the loop. A tick with no engine or no peers is
    a no-op — the engine only starts at login, and pushing to nobody is
    pointless."""
    while True:
        await asyncio.sleep(CATCHUP_INTERVAL_SECONDS)
        try:
            async with session_scope() as db:
                await push_snapshot_to_all_peers(db)
        except Exception:
            logger.exception("Catch-up sync tick failed")
