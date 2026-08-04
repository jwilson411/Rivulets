"""Shared publish-after-commit helper for entity API routes (FR-9.1).

Call after committing a local create/update, passing the entity's
already-committed field values as `payload` (only the fields the relevant
EntitySpec/apply function actually syncs need to be present — extras are
harmless, apply_remote_change ignores unknown keys). Best-effort: the sync
engine not running, or a downstream publish failure, must never fail the
request (FR-9.5) — logged and swallowed here so call sites don't each
repeat the same try/except.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.sync import get_sync_engine
from agent_hive.sync.apply import record_local_change

logger = logging.getLogger(__name__)


async def publish_entity_change(
    db: AsyncSession, entity_type: str, entity_id: str, payload: dict[str, Any]
) -> None:
    engine = get_sync_engine()
    if not engine.running:
        return
    try:
        vector_clock = await record_local_change(db, entity_type, entity_id, engine.node_id)
        await engine.publish_state_change(entity_type, entity_id, payload, vector_clock)
    except Exception:
        logger.warning(
            "Failed to publish sync change for %s/%s", entity_type, entity_id, exc_info=True
        )
