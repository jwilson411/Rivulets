"""Rivulet transcript wrapping — agents must see prior turns, not only
the triggering message."""

from rivulets.db.models import Message
from rivulets.dispatch.history import (
    HISTORY_MESSAGE_LIMIT,
    format_transcript,
    with_conversation_history,
    wrap_with_history,
)


def _msg(
    *,
    id: str,
    sender_name: str,
    content: str,
    sender_type: str = "human",
    content_type: str = "text",
) -> Message:
    return Message(
        id=id,
        rivulet_id="r1",
        sender_type=sender_type,
        sender_name=sender_name,
        content=content,
        content_type=content_type,
    )


def test_format_transcript_labels_speakers() -> None:
    text = format_transcript(
        [
            _msg(id="1", sender_name="Justin", content="write a parser"),
            _msg(id="2", sender_name="Assistant", content="Which language?", sender_type="agent"),
        ]
    )
    assert text == "Justin: write a parser\nAssistant: Which language?"


def test_format_transcript_skips_workflow_steps() -> None:
    text = format_transcript(
        [
            _msg(id="1", sender_name="system", content="step", content_type="workflow_step"),
            _msg(id="2", sender_name="Justin", content="hello"),
        ]
    )
    assert text == "Justin: hello"


def test_wrap_with_history_is_identity_when_empty() -> None:
    assert wrap_with_history("", "hello") == "hello"


def test_wrap_with_history_puts_current_turn_last() -> None:
    wrapped = wrap_with_history("Justin: hi", "follow up")
    assert wrapped.startswith("[Conversation so far]")
    assert wrapped.endswith("[Current message]\nfollow up")
    assert "Justin: hi" in wrapped


async def test_with_conversation_history_excludes_trigger(
    db_session: object,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from rivulets.db.models import Channel, Rivulet

    assert isinstance(db_session, AsyncSession)
    channel = Channel(name="hist-test")
    db_session.add(channel)
    await db_session.flush()
    rivulet = Rivulet(channel_id=channel.id, created_by="human")
    db_session.add(rivulet)
    await db_session.flush()
    first = Message(
        rivulet_id=rivulet.id,
        sender_type="human",
        sender_name="Justin",
        content="first turn",
    )
    second = Message(
        rivulet_id=rivulet.id,
        sender_type="agent",
        sender_name="Assistant",
        content="got it",
    )
    third = Message(
        rivulet_id=rivulet.id,
        sender_type="human",
        sender_name="Justin",
        content="second turn",
    )
    db_session.add_all([first, second, third])
    await db_session.commit()

    prompt = await with_conversation_history(
        db_session, rivulet.id, "second turn", exclude_message_id=third.id
    )
    assert "first turn" in prompt
    assert "got it" in prompt
    assert prompt.count("second turn") == 1
    assert HISTORY_MESSAGE_LIMIT >= 1
