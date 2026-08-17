"""Rivulet transcript injected into every agent run.

Agno sessions are per-agent: sharing `rivulet.agentos_session_id` does
not give Coder the messages Assistant already saw. Agents were therefore
invoked with only the triggering turn, which is why a specialist would
claim it had no prior conversation. The channel thread is the source of
truth — load it from Message rows and prepend it to the prompt.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Message

# Enough for a real thread without blowing a cheap-tier context window.
# Older turns fall off; the current message is always passed separately.
HISTORY_MESSAGE_LIMIT = 40

_SKIP_CONTENT_TYPES = frozenset({"workflow_step"})


def format_transcript(messages: list[Message]) -> str:
    """Human-readable channel log. One line per message; multiline
    bodies stay indented under the speaker so the current turn is
    still easy to find at the bottom."""
    lines: list[str] = []
    for message in messages:
        if message.content_type in _SKIP_CONTENT_TYPES:
            continue
        body = (message.content or "").strip()
        if not body:
            continue
        if message.content_type == "handoff":
            speaker = "system"
        elif message.content_type == "team_engaged":
            speaker = "system"
        elif message.content_type == "system_alert":
            speaker = "system"
        else:
            speaker = message.sender_name or message.sender_type
        if "\n" in body:
            indented = "\n".join(f"  {line}" for line in body.splitlines())
            lines.append(f"{speaker}:\n{indented}")
        else:
            lines.append(f"{speaker}: {body}")
    return "\n".join(lines)


def wrap_with_history(transcript: str, current_message: str) -> str:
    """Keep the triggering turn as the last thing the model sees."""
    if not transcript.strip():
        return current_message
    return (
        "[Conversation so far]\n"
        f"{transcript}\n"
        "\n"
        "[Current message]\n"
        f"{current_message}"
    )


async def with_conversation_history(
    db: AsyncSession,
    rivulet_id: str,
    current_message: str,
    *,
    exclude_message_id: str | None = None,
) -> str:
    """Prepend this rivulet's recent messages to `current_message`.

    `exclude_message_id` is the triggering row (already persisted before
    dispatch). Including it would duplicate the current turn.
    """
    result = await db.execute(
        select(Message)
        .where(Message.rivulet_id == rivulet_id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_MESSAGE_LIMIT + 1)
    )
    recent = list(result.scalars().all())
    recent.reverse()
    included = [m for m in recent if m.id != exclude_message_id]
    # If we over-fetched because the excluded row was in the window,
    # keep the oldest-N after filtering.
    if len(included) > HISTORY_MESSAGE_LIMIT:
        included = included[-HISTORY_MESSAGE_LIMIT:]
    return wrap_with_history(format_transcript(included), current_message)
