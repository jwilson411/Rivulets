"""Threads & messages (FR-5), including the SSE stream (api-design.md#sse-protocol).

Posting a message runs the real dispatcher (dispatch/service.py) against
the channel's team and persists any matched agents' replies in the same
request, publishing SSE events as it goes (FR-12.3) — a client with the
stream endpoint open sees agent_token/agent_message/system_alert/error
events live, in the same request cycle that's doing the dispatching.

Threads and messages are also synced (FR-9.1). This is the one place
where getting the sync boundary right actually matters for correctness,
not just data completeness: dispatch (agent invocation, LLM calls) only
ever runs as a side effect of *locally* handling a human-posted message
below. Applying an incoming remote message (sync/apply.py's generic
apply_remote_change, routed through MESSAGE_SPEC) is pure data
replication — it inserts a Message row and nothing else. If it also
re-ran dispatch, every node with the same team config would independently
answer the same human message, producing duplicate agent replies instead
of one. Human messages replicate for history; only the node that actually
received them from a human dispatches."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from agent_hive.api.deps import CurrentWorkspaceId, CurrentWorkspaceIdForStream, DbSession
from agent_hive.db.models import Channel, Message, Thread
from agent_hive.dispatch import dispatch_and_respond
from agent_hive.dispatch.guards import get_or_create_guard_state, reset_guard_state
from agent_hive.streaming import subscribe, unsubscribe
from agent_hive.sync.publish import publish_entity_change

router = APIRouter(tags=["threads"])

_DISCONNECT_POLL_SECONDS = 15


class MessageCreate(BaseModel):
    content: str
    files: list[str] = []


class MessageOut(BaseModel):
    id: str
    thread_id: str
    sender_type: str
    sender_id: str | None
    sender_name: str
    content: str
    content_type: str
    created_at: str

    model_config = {"from_attributes": True}


class ThreadOut(BaseModel):
    id: str
    channel_id: str
    title: str | None
    status: str
    created_by: str
    created_at: str

    model_config = {"from_attributes": True}


async def _get_channel_or_404(db: DbSession, channel_id: str) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    return channel


async def _get_thread_or_404(db: DbSession, thread_id: str) -> Thread:
    thread = await db.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    return thread


async def _publish_thread_change(db: DbSession, thread: Thread) -> None:
    await publish_entity_change(
        db,
        "thread",
        thread.id,
        {
            "channel_id": thread.channel_id,
            "title": thread.title,
            "status": thread.status,
            "created_by": thread.created_by,
        },
    )


async def _publish_message_change(db: DbSession, message: Message) -> None:
    await publish_entity_change(
        db,
        "message",
        message.id,
        {
            "thread_id": message.thread_id,
            "sender_type": message.sender_type,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "content": message.content,
            "content_type": message.content_type,
            "metadata_json": message.metadata_json,
        },
    )


@router.get("/channels/{channel_id}/threads", response_model=list[ThreadOut])
async def list_threads(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[Thread]:
    await _get_channel_or_404(db, channel_id)
    result = await db.execute(
        select(Thread).where(Thread.channel_id == channel_id).order_by(Thread.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/channels/{channel_id}/threads",
    response_model=ThreadOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    channel_id: str, body: MessageCreate, db: DbSession, _: CurrentWorkspaceId
) -> Thread:
    """Posting to the channel creates a thread with the human message as its
    root (FR-5.1), then dispatches it to the channel's team (FR-4.1)."""
    channel = await _get_channel_or_404(db, channel_id)
    thread = Thread(channel_id=channel_id, created_by="human")
    db.add(thread)
    await db.flush()
    human_message = Message(
        thread_id=thread.id,
        sender_type="human",
        sender_name="You",
        content=body.content,
    )
    db.add(human_message)
    agent_messages = await dispatch_and_respond(db, thread, channel, body.content)
    await db.commit()
    await db.refresh(thread)
    await _publish_thread_change(db, thread)
    await _publish_message_change(db, human_message)
    for agent_message in agent_messages:
        await _publish_message_change(db, agent_message)
    return thread


@router.get("/threads/{thread_id}", response_model=ThreadOut)
async def get_thread(thread_id: str, db: DbSession, _: CurrentWorkspaceId) -> Thread:
    return await _get_thread_or_404(db, thread_id)


@router.get("/threads/{thread_id}/messages", response_model=list[MessageOut])
async def list_messages(thread_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[Message]:
    await _get_thread_or_404(db, thread_id)
    result = await db.execute(
        select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    thread_id: str, body: MessageCreate, db: DbSession, _: CurrentWorkspaceId
) -> Message:
    thread = await _get_thread_or_404(db, thread_id)
    channel = await _get_channel_or_404(db, thread.channel_id)
    message = Message(
        thread_id=thread_id, sender_type="human", sender_name="You", content=body.content
    )
    db.add(message)
    # dispatch_and_respond resets ThreadGuardState on every human-triggered
    # call (FR-7.5) before dispatching.
    agent_messages = await dispatch_and_respond(db, thread, channel, body.content)
    await db.commit()
    await db.refresh(message)
    # dispatch can pause the thread (a loop guard tripping) as a side
    # effect, so its state needs republishing here too, not just from the
    # explicit resume/close endpoints below.
    await _publish_thread_change(db, thread)
    await _publish_message_change(db, message)
    for agent_message in agent_messages:
        await _publish_message_change(db, agent_message)
    return message


@router.post("/threads/{thread_id}/resume", response_model=ThreadOut)
async def resume_thread(thread_id: str, db: DbSession, _: CurrentWorkspaceId) -> Thread:
    """FR-7.5's explicit "Resume" affordance — equivalent to what posting
    any message already does, for when a human just wants to clear a
    pause without saying anything yet."""
    thread = await _get_thread_or_404(db, thread_id)
    thread.status = "active"
    guard_state = await get_or_create_guard_state(db, thread_id)
    reset_guard_state(guard_state)
    await db.commit()
    await db.refresh(thread)
    await _publish_thread_change(db, thread)
    return thread


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_thread(thread_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    thread = await _get_thread_or_404(db, thread_id)
    thread.status = "closed"
    await db.commit()
    await _publish_thread_change(db, thread)


@router.get("/threads/{thread_id}/stream")
async def stream_thread(
    thread_id: str, request: Request, db: DbSession, _: CurrentWorkspaceIdForStream
) -> StreamingResponse:
    """SSE endpoint (api-design.md#sse-protocol), backed by streaming.py's
    in-process pub/sub. Stays open for the life of the connection — a
    client viewing a thread gets every dispatch round's events as they
    happen, not just the first one, so this never sends a terminal event
    of its own; the generator only exits on client disconnect.

    Emits agent_token, agent_message, system_alert, error, and done.
    agent_tool_call and handoff aren't emitted yet — built-in tool calls
    aren't wired into agent construction (FR-8.2's TODO in agentos/
    service.py) and the handoff tool (FR-6) doesn't exist yet, so neither
    has anything to observe.

    The DB session this pulls in via dependency injection stays open for
    as long as the connection does, same as the subscription — acceptable
    overhead for a local single-user SQLite-backed app, not worth the
    added complexity of releasing it early for one existence check.
    """
    await _get_thread_or_404(db, thread_id)
    queue = subscribe(thread_id)

    async def event_source() -> AsyncIterator[bytes]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_DISCONNECT_POLL_SECONDS)
                except TimeoutError:
                    continue
                payload = json.dumps(event["data"])
                yield f"event: {event['event']}\ndata: {payload}\n\n".encode()
        finally:
            unsubscribe(thread_id, queue)

    return StreamingResponse(event_source(), media_type="text/event-stream")
