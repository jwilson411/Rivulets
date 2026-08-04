"""Vector-clock conflict resolution for synced entities (FR-9.6).

Each entity carries two related but distinct clocks:
  - `Entity.vector_clock` (an int column on Agent, Channel, etc.): a
    cheap, human-visible "highest version I've seen" cache — not itself
    sufficient to detect concurrent edits across nodes.
  - `VectorClockTracker(entity_type, entity_id, node_id, clock)`: the real
    per-node components of a proper vector clock. Comparing the *full set*
    of components between a local and an incoming version is what
    distinguishes "remote strictly happened after local" (safe to
    apply — FR-9.6's last-write-wins case) from "these were edited
    concurrently on two disconnected nodes" (a genuine conflict, recorded
    as a SyncConflict rather than silently overwritten, per FR-9.6's
    second sentence: conflicts must be surfaced for inspection, not
    resolved by fiat).

Only the `agent` entity type is wired end-to-end so far — FR-9's thin
first slice (FR-9.1 lists the full sync scope: agents, channels, threads,
tools, MCP servers, files, settings). This module's shape generalizes to
each of those once they get their own apply function; nothing here is
agent-specific except `apply_remote_agent_change`'s field list.
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.db.models import Agent, SyncConflict, VectorClockTracker
from agent_hive.db.session import session_scope

logger = logging.getLogger(__name__)

_AGENT_SYNCED_FIELDS = ("name", "description", "instructions", "model")


class ClockComparison(Enum):
    LOCAL_NEWER = "local_newer"  # local strictly dominates remote -- ignore remote
    REMOTE_NEWER = "remote_newer"  # remote strictly dominates local -- apply remote
    EQUAL = "equal"  # identical -- no-op (duplicate delivery)
    CONCURRENT = "concurrent"  # neither dominates -- conflict


def compare_vector_clocks(local: dict[str, int], remote: dict[str, int]) -> ClockComparison:
    keys = local.keys() | remote.keys()
    local_ge = all(local.get(k, 0) >= remote.get(k, 0) for k in keys)
    remote_ge = all(remote.get(k, 0) >= local.get(k, 0) for k in keys)
    if local_ge and remote_ge:
        return ClockComparison.EQUAL
    if remote_ge:
        return ClockComparison.REMOTE_NEWER
    if local_ge:
        return ClockComparison.LOCAL_NEWER
    return ClockComparison.CONCURRENT


def merge_vector_clocks(local: dict[str, int], remote: dict[str, int]) -> dict[str, int]:
    return {k: max(local.get(k, 0), remote.get(k, 0)) for k in local.keys() | remote.keys()}


async def _load_vector_clock(db: AsyncSession, entity_type: str, entity_id: str) -> dict[str, int]:
    result = await db.execute(
        select(VectorClockTracker).where(
            VectorClockTracker.entity_type == entity_type,
            VectorClockTracker.entity_id == entity_id,
        )
    )
    return {row.node_id: row.clock for row in result.scalars().all()}


async def _store_vector_clock(
    db: AsyncSession, entity_type: str, entity_id: str, vector_clock: dict[str, int]
) -> None:
    for node_id, clock in vector_clock.items():
        row = await db.get(VectorClockTracker, (entity_type, entity_id, node_id))
        if row is None:
            db.add(
                VectorClockTracker(
                    entity_type=entity_type, entity_id=entity_id, node_id=node_id, clock=clock
                )
            )
        else:
            row.clock = clock


async def record_local_change(
    db: AsyncSession, entity_type: str, entity_id: str, own_node_id: str
) -> dict[str, int]:
    """Call after committing a local create/update, before publishing to
    peers. Bumps this node's own vector-clock component and returns the
    full vector clock to include in the gossipsub payload — the whole
    point of a vector clock is that every message carries the full vector,
    not just the sender's own component, so recipients can do a real
    happened-before comparison."""
    vc = await _load_vector_clock(db, entity_type, entity_id)
    vc[own_node_id] = vc.get(own_node_id, 0) + 1
    await _store_vector_clock(db, entity_type, entity_id, vc)
    await db.commit()
    return vc


@dataclass(frozen=True, slots=True)
class ApplyResult:
    applied: bool
    conflict: bool


def _agent_snapshot(agent: Agent) -> dict[str, str]:
    return {field: getattr(agent, field) for field in _AGENT_SYNCED_FIELDS}


async def apply_remote_agent_change(
    db: AsyncSession,
    entity_id: str,
    remote_vector_clock: dict[str, int],
    remote_node_id: str,
    payload: dict[str, str],
) -> ApplyResult:
    """FR-9.6: apply an incoming agent change if it strictly descends from
    what this node already knows about that entity; ignore it if this node
    is already ahead (stale/duplicate message); record a SyncConflict
    (touching no local data) if the two versions are concurrent."""
    local_vc = await _load_vector_clock(db, "agent", entity_id)
    comparison = compare_vector_clocks(local_vc, remote_vector_clock)

    if comparison in (ClockComparison.EQUAL, ClockComparison.LOCAL_NEWER):
        return ApplyResult(applied=False, conflict=False)

    merged = merge_vector_clocks(local_vc, remote_vector_clock)

    if comparison is ClockComparison.CONCURRENT:
        local_agent = await db.get(Agent, entity_id)
        db.add(
            SyncConflict(
                entity_type="agent",
                entity_id=entity_id,
                local_snapshot=json.dumps(_agent_snapshot(local_agent) if local_agent else {}),
                remote_snapshot=json.dumps(payload),
                remote_node_id=remote_node_id,
            )
        )
        # Vector-clock bookkeeping still advances even though we didn't
        # apply the payload: this node is now causally aware of both
        # versions, which is what lets a *future* incoming message be
        # correctly judged against what's already been seen.
        await _store_vector_clock(db, "agent", entity_id, merged)
        await db.commit()
        return ApplyResult(applied=False, conflict=True)

    # REMOTE_NEWER: a clean, non-conflicting update -- apply it.
    agent = await db.get(Agent, entity_id)
    if agent is None:
        agent = Agent(id=entity_id, name="", description="", instructions="", model="")
        db.add(agent)
    for field in _AGENT_SYNCED_FIELDS:
        if field in payload:
            setattr(agent, field, payload[field])
    agent.vector_clock = max(merged.values())
    await _store_vector_clock(db, "agent", entity_id, merged)
    await db.commit()
    return ApplyResult(applied=True, conflict=False)


async def handle_incoming_state_change(
    entity_type: str,
    entity_id: str,
    vector_clock: dict[str, int],
    origin_node_id: str,
    payload: dict[str, Any],
) -> None:
    """SyncEngine's state-change handler (engine.py's `StateChangeHandler`
    type) — the entry point called via `asyncio.run_coroutine_threadsafe`
    from the trio thread whenever a gossipsub message arrives. Opens its
    own DB session since it isn't running inside a FastAPI request.

    Only `agent` is wired up (FR-9's thin first slice); other entity types
    listed in FR-9.1 are logged and dropped until they get their own apply
    function, matching this module's stated generalization path."""
    if entity_type != "agent":
        logger.info("Dropping sync message for unsupported entity_type=%r", entity_type)
        return
    async with session_scope() as db:
        result = await apply_remote_agent_change(
            db, entity_id, vector_clock, origin_node_id, payload
        )
        if result.conflict:
            logger.warning("Sync conflict recorded for agent/%s", entity_id)
        elif result.applied:
            logger.info("Applied remote change for agent/%s from %s", entity_id, origin_node_id)
