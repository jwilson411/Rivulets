"""Loop prevention guards (FR-7): turn limits, cycle detection, and
timeout-based pause for agent-to-agent exchanges within a thread.

State lives in `thread_guard_state` (data-model.md) — one row per thread,
not synced between nodes (each node tracks its own local agent activity).
Thresholds are workspace settings (FR-7.4) with the same keys and
defaults api/settings.py already declares; read directly here rather than
importing that module, since this only needs four scalars, not the full
settings surface.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import Message, ThreadGuardState, WorkspaceSetting

_GUARD_DEFAULTS: dict[str, int] = {
    "guard.turn_limit": 10,
    "guard.cycle_window": 8,
    "guard.cycle_threshold": 3,
    "guard.timeout_minutes": 30,
}

TURN_LIMIT_MESSAGE = "Agent conversation has reached the turn limit. Waiting for human input."


async def _get_guard_setting(db: AsyncSession, key: str) -> int:
    row = await db.get(WorkspaceSetting, key)
    if row is None:
        return _GUARD_DEFAULTS[key]
    return int(json.loads(row.value))


async def get_or_create_guard_state(db: AsyncSession, thread_id: str) -> ThreadGuardState:
    state = await db.get(ThreadGuardState, thread_id)
    if state is None:
        state = ThreadGuardState(thread_id=thread_id)
        db.add(state)
        await db.flush()
    return state


def reset_guard_state(state: ThreadGuardState) -> None:
    """FR-7.5: any human message resets all counters and resumes a paused
    thread, whatever tripped it."""
    state.agent_exchange_count = 0
    state.recent_interactions = None
    state.agent_active_since = None
    state.paused = False
    state.paused_at = None
    state.pause_reason = None


async def record_agent_message(
    db: AsyncSession,
    thread_id: str,
    state: ThreadGuardState,
    *,
    from_agent_id: str | None,
    from_agent_name: str,
    to_agent_id: str,
    to_agent_name: str,
) -> Message | None:
    """Update guard counters after an agent message is about to be
    persisted, and check all three limits (FR-7.1/7.2/7.3). Returns a
    system_alert Message if a limit was just tripped — the caller must
    stop dispatching further in this thread once it gets one back.

    `from_agent_id` is the previous speaker in this chain: None if the
    triggering message was from a human (no agent-to-agent pair to
    record), an agent ID if this is a recursive re-dispatch off another
    agent's own message (see dispatch/service.py).
    """
    state.agent_exchange_count += 1
    if state.agent_active_since is None:
        state.agent_active_since = utcnow_iso()

    if from_agent_id is not None:
        pairs: list[list[str]] = (
            json.loads(state.recent_interactions) if state.recent_interactions else []
        )
        pairs.append([from_agent_id, to_agent_id])
        window = await _get_guard_setting(db, "guard.cycle_window")
        pairs = pairs[-window:]
        state.recent_interactions = json.dumps(pairs)

        threshold = await _get_guard_setting(db, "guard.cycle_threshold")
        if pairs.count([from_agent_id, to_agent_id]) >= threshold:
            return _pause(
                state,
                "cycle_detected",
                f"Detected a repeating loop between @{from_agent_name} and @{to_agent_name} "
                "— pausing until a human responds.",
                thread_id,
            )

    turn_limit = await _get_guard_setting(db, "guard.turn_limit")
    if state.agent_exchange_count >= turn_limit:
        return _pause(state, "turn_limit", TURN_LIMIT_MESSAGE, thread_id)

    timeout_minutes = await _get_guard_setting(db, "guard.timeout_minutes")
    active_since = datetime.fromisoformat(state.agent_active_since.replace("Z", "+00:00"))
    if datetime.now(UTC) - active_since > timedelta(minutes=timeout_minutes):
        return _pause(
            state,
            "timeout",
            f"Agents have been active for over {timeout_minutes} minutes without "
            "human input — pausing until a human responds.",
            thread_id,
        )

    return None


def _pause(state: ThreadGuardState, reason: str, message: str, thread_id: str) -> Message:
    state.paused = True
    state.paused_at = utcnow_iso()
    state.pause_reason = reason
    return Message(
        thread_id=thread_id,
        sender_type="system",
        sender_name="system",
        content=message,
        content_type="system_alert",
    )
