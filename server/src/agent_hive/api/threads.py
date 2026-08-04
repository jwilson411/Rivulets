"""Threads & messages (FR-5), including the SSE stream (api-design.md#sse-protocol).

Posting a message runs the real dispatcher (dispatch/service.py) against
the channel's team and persists any matched agents' replies in the same
request, publishing SSE events as it goes (FR-12.3) — a client with the
stream endpoint open sees agent_token/agent_message/system_alert/error
events live, in the same request cycle that's doing the dispatching.
"""

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
    db.add(
        Message(
            thread_id=thread.id,
            sender_type="human",
            sender_name="You",
            content=body.content,
        )
    )
    await dispatch_and_respond(db, thread, channel, body.content)
    await db.commit()
    await db.refresh(thread)
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
    await dispatch_and_respond(db, thread, channel, body.content)
    await db.commit()
    await db.refresh(message)
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
    return thread


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_thread(thread_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    thread = await _get_thread_or_404(db, thread_id)
    thread.status = "closed"
    await db.commit()


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
