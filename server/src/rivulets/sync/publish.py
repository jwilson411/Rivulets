"""Shared publish-after-commit helpers for entity API routes (FR-9.1).

`publish_entity_change` is the low-level primitive: publish an
already-known payload, best-effort — the sync engine not running, or a
downstream publish failure, must never fail the request (FR-9.5). Both
failure modes now queue the entity for retry (SyncPendingOutbound)
instead of just logging and dropping the change forever, which is what
"changes sync automatically when connectivity resumes" (FR-9.5) actually
requires — `drain_pending_outbound` is called once the engine starts
(api/auth.py's login) and re-publishes anything still queued.

`build_entity_payload` is what makes the retry path possible without
replaying a possibly-stale payload: given just (entity_type, entity_id)
— all a SyncPendingOutbound row stores — it re-reads current DB state and
reconstructs the payload fresh, the same way api/agents.py, api/
channels.py, etc. each used to do by hand. Every `_publish_*_change`
helper across the API layer now calls `publish_current_state` instead of
building its own payload dict — one less place for a payload to drift out
of sync with its EntitySpec's synced_fields (or Tool/File's bespoke
field list) as those change over time.
"""

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import File, SyncPendingOutbound, Tool
from rivulets.sync import get_sync_engine
from rivulets.sync.apply import TOMBSTONE_FIELD, get_entity_spec, record_local_change

logger = logging.getLogger(__name__)


async def publish_entity_change(
    db: AsyncSession, entity_type: str, entity_id: str, payload: dict[str, Any]
) -> None:
    engine = get_sync_engine()
    if not engine.running:
        await _record_pending_outbound(db, entity_type, entity_id)
        return
    try:
        vector_clock = await record_local_change(db, entity_type, entity_id, engine.node_id)
        await engine.publish_state_change(entity_type, entity_id, payload, vector_clock)
    except Exception:
        logger.warning(
            "Failed to publish sync change for %s/%s", entity_type, entity_id, exc_info=True
        )
        await _record_pending_outbound(db, entity_type, entity_id)


async def publish_tombstone(db: AsyncSession, entity_type: str, entity_id: str) -> None:
    """The delete counterpart to publish_current_state (#238): called after
    a local hard-delete has already been committed, so unlike
    publish_current_state there's no live row left to rebuild a payload
    from. Bumps the vector clock the same way any edit does -- via the
    same record_local_change an edit uses -- so a peer's concurrent,
    not-yet-seen edit of the same entity is judged CONCURRENT rather than
    silently overwritten either way (see apply.py's apply_remote_delete for
    the receiving side and why that's what makes "peer edits resurrect
    deleted entities" no longer happen). Best-effort like
    publish_entity_change: engine down or a publish failure queues a
    tombstone retry (SyncPendingOutbound.deleted) instead of dropping the
    delete forever."""
    engine = get_sync_engine()
    if not engine.running:
        await _record_pending_outbound(db, entity_type, entity_id, deleted=True)
        return
    try:
        vector_clock = await record_local_change(db, entity_type, entity_id, engine.node_id)
        await engine.publish_state_change(
            entity_type, entity_id, {TOMBSTONE_FIELD: True}, vector_clock
        )
    except Exception:
        logger.warning(
            "Failed to publish sync tombstone for %s/%s", entity_type, entity_id, exc_info=True
        )
        await _record_pending_outbound(db, entity_type, entity_id, deleted=True)


async def publish_current_state(db: AsyncSession, entity_type: str, entity_id: str) -> None:
    """Convenience wrapper for call sites that don't already have a
    payload dict in hand (or want the guarantee that what's published
    always exactly matches the entity's current synced fields): re-reads
    the entity and publishes it. A no-op if the entity no longer exists or
    isn't in a syncable state (e.g. a non-custom Tool)."""
    payload = await build_entity_payload(db, entity_type, entity_id)
    if payload is not None:
        await publish_entity_change(db, entity_type, entity_id, payload)


async def build_entity_payload(
    db: AsyncSession, entity_type: str, entity_id: str
) -> dict[str, Any] | None:
    """Reconstructs the current publish payload for any synced entity type
    from local DB state. Returns None if there's nothing to publish (the
    entity was deleted, or — for Tool — isn't a synced tool_type)."""
    if entity_type == "tool":
        return await _build_tool_payload(db, entity_id)
    if entity_type == "file":
        return await _build_file_payload(db, entity_id)
    spec = get_entity_spec(entity_type)
    if spec is None:
        return None
    instance = await db.get(spec.model, entity_id)
    if instance is None:
        return None
    return {field: getattr(instance, field) for field in spec.synced_fields}


async def _build_tool_payload(db: AsyncSession, entity_id: str) -> dict[str, Any] | None:
    tool = await db.get(Tool, entity_id)
    if tool is None or tool.tool_type != "custom":
        return None
    payload: dict[str, Any] = {"name": tool.name, "description": tool.description}
    if tool.source_path:
        try:
            payload["source_code"] = Path(tool.source_path).read_text(encoding="utf-8")
        except OSError:
            pass  # no file yet (e.g. immediately after create) -- sync metadata only
    return payload


async def _build_file_payload(db: AsyncSession, entity_id: str) -> dict[str, Any] | None:
    file_row = await db.get(File, entity_id)
    if file_row is None:
        return None
    return {
        "content_hash": file_row.content_hash,
        "filename": file_row.filename,
        "mime_type": file_row.mime_type,
        "size_bytes": file_row.size_bytes,
        "message_id": file_row.message_id,
    }


async def _record_pending_outbound(
    db: AsyncSession, entity_type: str, entity_id: str, *, deleted: bool = False
) -> None:
    existing = await db.get(SyncPendingOutbound, (entity_type, entity_id))
    if existing is None:
        db.add(SyncPendingOutbound(entity_type=entity_type, entity_id=entity_id, deleted=deleted))
        await db.commit()
    elif deleted and not existing.deleted:
        # A live-state publish was already queued for this entity when it
        # got deleted before that retry ever ran (#238) -- the queued
        # retry would otherwise call publish_current_state, which is a
        # silent no-op for a deleted entity (build_entity_payload returns
        # None), losing the delete rather than ever telling peers about
        # it. One row per entity either way; promote it in place instead
        # of adding a second.
        existing.deleted = True
        await db.commit()


async def drain_pending_outbound(db: AsyncSession) -> None:
    """Called once the sync engine starts (api/auth.py's login). Retries
    every entity queued by a previous failed/impossible publish attempt.
    Each row is deleted before its retry is attempted, not after: if the
    retry fails again, publish_entity_change's/publish_tombstone's own
    failure path re-queues it, so there's no window where a row is
    silently dropped because this function's own bookkeeping (rather than
    the publish itself) failed."""
    engine = get_sync_engine()
    if not engine.running:
        return
    result = await db.execute(select(SyncPendingOutbound))
    pending = list(result.scalars().all())
    for row in pending:
        entity_type, entity_id, deleted = row.entity_type, row.entity_id, row.deleted
        await db.delete(row)
        await db.commit()
        if deleted:
            await publish_tombstone(db, entity_type, entity_id)
        else:
            await publish_current_state(db, entity_type, entity_id)
    if pending:
        logger.info("Retried %d pending outbound sync change(s)", len(pending))
