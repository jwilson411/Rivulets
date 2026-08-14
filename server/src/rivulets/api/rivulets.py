"""Rivulets & messages (FR-5), including the SSE stream (api-design.md#sse-protocol).

Posting a message runs the real dispatcher (dispatch/service.py) against
the channel's team and persists any matched agents' replies in the same
request, publishing SSE events as it goes (FR-12.3) — a client with the
stream endpoint open sees agent_token/agent_message/system_alert/error
events live, in the same request cycle that's doing the dispatching.

Before that, both message-posting endpoints below check whether the
content is a `/workflow-name <input>` trigger (#24,
workflows/trigger.py's find_triggered_workflow) — if so, the message runs
that workflow (workflows/engine.py) instead of the dispatcher entirely; a
slash-command-shaped message that doesn't match a real workflow name
falls through to ordinary dispatch unchanged.

post_message checks one more thing first, ahead of that: whether this
rivulet has a workflow paused on a 'human_input' node (#83,
find_awaiting_workflow_run) — if so, whatever the human just typed is
unambiguously the reply (workflows/engine.py's resume_workflow), not a
fresh trigger or a dispatcher message. create_rivulet doesn't need this
check: a workflow can only pause on a rivulet that already exists.

Rivulets and messages are also synced (FR-9.1). This is the one place
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
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from rivulets.api.deps import (
    CurrentHumanId,
    CurrentStreamClaims,
    CurrentWorkspaceId,
    DbSession,
)
from rivulets.api.files import publish_file_change
from rivulets.db.models import Channel, File, Human, Message, Rivulet
from rivulets.dispatch import dispatch_and_respond
from rivulets.dispatch.guards import get_or_create_guard_state, reset_guard_state
from rivulets.streaming import subscribe, unsubscribe
from rivulets.sync.publish import publish_current_state
from rivulets.tracing import finish_trace, start_trace
from rivulets.workflows import (
    find_awaiting_workflow_run,
    find_triggered_workflow,
    resume_workflow,
    run_workflow,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rivulets"])

_DISCONNECT_POLL_SECONDS = 15


class MessageCreate(BaseModel):
    # Generous but bounded -- large enough for a pasted log/stack trace,
    # small enough that one message can't blow up DB row size or an
    # agent's context budget on its own (attachments are the intended path
    # for genuinely large content, via `files` below).
    content: str = Field(max_length=100_000)
    files: list[str] = []  # file_ids of already-uploaded files (POST /files/upload) to attach


class AttachmentOut(BaseModel):
    file_id: str
    filename: str
    mime_type: str
    size_bytes: int


class MessageOut(BaseModel):
    id: str
    rivulet_id: str
    sender_type: str
    sender_id: str | None
    sender_name: str
    content: str
    content_type: str
    created_at: str
    attachments: list[AttachmentOut] = []
    # Auto mode (#23) visibility: which concrete model answered, and which
    # tier it was classified into. Only set on replies from an "auto"
    # agent -- parsed here from Message.metadata_json rather than exposing
    # that raw JSON column directly, since a UI badge only needs these two
    # fields, not a general-purpose metadata bag.
    model_used: str | None = None
    tier: str | None = None
    # Issue #10: which node actually ran the agent that produced this
    # message -- None for human messages, or when the sync engine wasn't
    # running at reply time. Parsed from the same metadata_json bag as
    # model_used/tier above.
    executed_node_id: str | None = None
    # #103: set only when this agent's configured fallback chain served
    # the reply because its primary model's call failed with a
    # retryable-looking error -- the model that actually answered. None
    # on every reply that didn't need to fall back.
    served_model: str | None = None

    model_config = {"from_attributes": True}


class RivuletOut(BaseModel):
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


async def _get_rivulet_or_404(db: DbSession, rivulet_id: str) -> Rivulet:
    rivulet = await db.get(Rivulet, rivulet_id)
    if rivulet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rivulet not found")
    return rivulet


async def _get_human_or_404(db: DbSession, human_id: str) -> Human:
    human = await db.get(Human, human_id)
    if human is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Human not found")
    return human


async def _publish_rivulet_change(db: DbSession, rivulet: Rivulet) -> None:
    await publish_current_state(db, "rivulet", rivulet.id)


async def _attach_files(db: DbSession, message: Message, file_ids: list[str]) -> list[File]:
    """FR-10.1's "into rivulets" — links already-uploaded files (POST
    /files/upload) to the message referencing them. Unknown file_ids are
    skipped with a warning rather than failing the whole post: a stale/
    bogus id from the client shouldn't block the human's message.

    message_id is a single nullable column, not a many-to-many join — a
    file can only ever belong to one message. Re-sending a file_id that's
    already attached elsewhere is also skipped-with-a-warning rather than
    reassigning it: silently moving a file from one message to another
    would make it vanish from the first message's attachment list with no
    trace of why. (Re-sending the same file_id for the *same* message is a
    harmless no-op, not an error — an idempotent retry shouldn't fail.)"""
    attached: list[File] = []
    for file_id in file_ids:
        file_row = await db.get(File, file_id)
        if file_row is None:
            logger.warning("Ignoring unknown file_id %r in message attachment", file_id)
            continue
        if file_row.message_id is not None and file_row.message_id != message.id:
            logger.warning("Ignoring file_id %r already attached to a different message", file_id)
            continue
        file_row.message_id = message.id
        attached.append(file_row)
    return attached


async def _publish_message_change(db: DbSession, message: Message) -> None:
    await publish_current_state(db, "message", message.id)


def _to_attachment_out(file_row: File) -> AttachmentOut:
    return AttachmentOut(
        file_id=file_row.id,
        filename=file_row.filename,
        mime_type=file_row.mime_type,
        size_bytes=file_row.size_bytes,
    )


def _to_message_out(message: Message, attachments: list[File]) -> MessageOut:
    metadata: dict[str, str] = json.loads(message.metadata_json) if message.metadata_json else {}
    return MessageOut(
        id=message.id,
        rivulet_id=message.rivulet_id,
        sender_type=message.sender_type,
        sender_id=message.sender_id,
        sender_name=message.sender_name,
        content=message.content,
        content_type=message.content_type,
        created_at=message.created_at,
        attachments=[_to_attachment_out(f) for f in attachments],
        model_used=metadata.get("model_used"),
        tier=metadata.get("tier"),
        executed_node_id=metadata.get("executed_node_id"),
        served_model=metadata.get("served_model"),
    )


async def _attachments_by_message(db: DbSession, message_ids: list[str]) -> dict[str, list[File]]:
    """File.message_id has no FK constraint (see this module's own docstring
    on why rivulets/messages sync the way they do — File mirrors that same
    loose-coupling choice), so this is a plain filter, not a join."""
    if not message_ids:
        return {}
    result = await db.execute(select(File).where(File.message_id.in_(message_ids)))
    grouped: dict[str, list[File]] = {}
    for file_row in result.scalars().all():
        assert file_row.message_id is not None  # guaranteed by the IN filter above
        grouped.setdefault(file_row.message_id, []).append(file_row)
    return grouped


@router.get("/channels/{channel_id}/rivulets", response_model=list[RivuletOut])
async def list_rivulets(channel_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[Rivulet]:
    await _get_channel_or_404(db, channel_id)
    result = await db.execute(
        select(Rivulet).where(Rivulet.channel_id == channel_id).order_by(Rivulet.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/channels/{channel_id}/rivulets",
    response_model=RivuletOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_rivulet(
    channel_id: str,
    body: MessageCreate,
    db: DbSession,
    _: CurrentWorkspaceId,
    human_id: CurrentHumanId,
) -> Rivulet:
    """Posting to the channel creates a rivulet with the human message as its
    root (FR-5.1), then dispatches it to the channel's team (FR-4.1)."""
    channel = await _get_channel_or_404(db, channel_id)
    human = await _get_human_or_404(db, human_id)
    rivulet = Rivulet(channel_id=channel_id, created_by="human")
    db.add(rivulet)
    await db.flush()
    human_message = Message(
        rivulet_id=rivulet.id,
        sender_type="human",
        sender_id=human.id,
        sender_name=human.display_name,
        content=body.content,
    )
    db.add(human_message)
    await db.flush()  # populate human_message.id before attaching files to it
    attached_files = await _attach_files(db, human_message, body.files)

    triggered = await find_triggered_workflow(db, body.content)
    if triggered is not None:
        workflow, workflow_input = triggered
        await db.commit()
        await db.refresh(rivulet)
        await _publish_rivulet_change(db, rivulet)
        await _publish_message_change(db, human_message)
        for file_row in attached_files:
            await publish_file_change(db, file_row)
        trace_ctx = await start_trace(
            db,
            trigger_type="message",
            label=body.content,
            rivulet_id=rivulet.id,
            channel_id=channel.id,
        )
        await run_workflow(
            db,
            workflow,
            rivulet,
            workflow_input,
            triggered_by="human",
            triggered_by_id=human.id,
            trace_ctx=trace_ctx,
        )
        await finish_trace(db, trace_ctx.trace_id)
        await db.commit()
        await db.refresh(rivulet)
        return rivulet

    # #237: commit the human message (and its attachments) before dispatch
    # runs -- dispatch_and_respond's own agent invocations hold a SQLite
    # write lock only around short, LLM-call-free critical sections, but
    # that only helps if it isn't handed an already-open transaction to
    # extend. Matches the triggered-workflow branch above.
    await db.commit()
    await db.refresh(rivulet)
    await _publish_rivulet_change(db, rivulet)
    await _publish_message_change(db, human_message)
    for file_row in attached_files:
        await publish_file_change(db, file_row)

    trace_ctx = await start_trace(
        db, trigger_type="message", label=body.content, rivulet_id=rivulet.id, channel_id=channel.id
    )
    agent_messages = await dispatch_and_respond(
        db,
        rivulet,
        channel,
        body.content,
        triggering_message_id=human_message.id,
        trace_ctx=trace_ctx,
        attached_files=attached_files,
    )
    await finish_trace(db, trace_ctx.trace_id)
    await db.commit()
    await db.refresh(rivulet)
    await _publish_rivulet_change(db, rivulet)  # dispatch can pause the rivulet as a side effect
    for agent_message in agent_messages:
        await _publish_message_change(db, agent_message)
    return rivulet


@router.get("/rivulets/{rivulet_id}", response_model=RivuletOut)
async def get_rivulet(rivulet_id: str, db: DbSession, _: CurrentWorkspaceId) -> Rivulet:
    return await _get_rivulet_or_404(db, rivulet_id)


@router.get("/rivulets/{rivulet_id}/messages", response_model=list[MessageOut])
async def list_messages(rivulet_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[MessageOut]:
    await _get_rivulet_or_404(db, rivulet_id)
    result = await db.execute(
        select(Message).where(Message.rivulet_id == rivulet_id).order_by(Message.created_at)
    )
    messages = list(result.scalars().all())
    attachments_by_message = await _attachments_by_message(db, [m.id for m in messages])
    return [_to_message_out(m, attachments_by_message.get(m.id, [])) for m in messages]


@router.post(
    "/rivulets/{rivulet_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    rivulet_id: str,
    body: MessageCreate,
    db: DbSession,
    _: CurrentWorkspaceId,
    human_id: CurrentHumanId,
) -> MessageOut:
    rivulet = await _get_rivulet_or_404(db, rivulet_id)
    channel = await _get_channel_or_404(db, rivulet.channel_id)
    human = await _get_human_or_404(db, human_id)
    message = Message(
        rivulet_id=rivulet_id,
        sender_type="human",
        sender_id=human.id,
        sender_name=human.display_name,
        content=body.content,
    )
    db.add(message)
    await db.flush()  # populate message.id before attaching files to it
    attached_files = await _attach_files(db, message, body.files)

    awaiting_run = await find_awaiting_workflow_run(db, rivulet_id)
    if awaiting_run is not None:
        await db.commit()
        await db.refresh(message)
        await resume_workflow(db, awaiting_run, body.content)
        await _publish_rivulet_change(db, rivulet)
        await _publish_message_change(db, message)
        for file_row in attached_files:
            await publish_file_change(db, file_row)
        return _to_message_out(message, attached_files)

    triggered = await find_triggered_workflow(db, body.content)
    if triggered is not None:
        workflow, workflow_input = triggered
        await db.commit()
        await db.refresh(message)
        await _publish_rivulet_change(db, rivulet)
        await _publish_message_change(db, message)
        for file_row in attached_files:
            await publish_file_change(db, file_row)
        trace_ctx = await start_trace(
            db,
            trigger_type="message",
            label=body.content,
            rivulet_id=rivulet.id,
            channel_id=channel.id,
        )
        await run_workflow(
            db,
            workflow,
            rivulet,
            workflow_input,
            triggered_by="human",
            triggered_by_id=human.id,
            trace_ctx=trace_ctx,
        )
        await finish_trace(db, trace_ctx.trace_id)
        await db.commit()
        return _to_message_out(message, attached_files)

    # #237: commit the human message (and its attachments) before dispatch
    # runs, matching the two triggered-workflow branches above -- otherwise
    # this open transaction is the one dispatch_and_respond's own agent
    # invocations would extend across their LLM calls.
    await db.commit()
    await db.refresh(message)
    await _publish_message_change(db, message)
    for file_row in attached_files:
        await publish_file_change(db, file_row)

    # dispatch_and_respond resets RivuletGuardState on every human-triggered
    # call (FR-7.5) before dispatching.
    trace_ctx = await start_trace(
        db, trigger_type="message", label=body.content, rivulet_id=rivulet.id, channel_id=channel.id
    )
    agent_messages = await dispatch_and_respond(
        db,
        rivulet,
        channel,
        body.content,
        triggering_message_id=message.id,
        trace_ctx=trace_ctx,
        attached_files=attached_files,
    )
    await finish_trace(db, trace_ctx.trace_id)
    await db.commit()
    # dispatch can pause the rivulet (a loop guard tripping) as a side
    # effect, so its state needs republishing here too, not just from the
    # explicit resume/close endpoints below.
    await db.refresh(rivulet)
    await _publish_rivulet_change(db, rivulet)
    for agent_message in agent_messages:
        await _publish_message_change(db, agent_message)
    return _to_message_out(message, attached_files)


@router.post("/rivulets/{rivulet_id}/resume", response_model=RivuletOut)
async def resume_rivulet(rivulet_id: str, db: DbSession, _: CurrentWorkspaceId) -> Rivulet:
    """FR-7.5's explicit "Resume" affordance — equivalent to what posting
    any message already does, for when a human just wants to clear a
    pause without saying anything yet. Refuses if a workflow is paused
    here (#83): unlike a loop-guard pause, a 'human_input' node's pause
    isn't clearable without an actual reply value to feed it as output --
    flipping Rivulet.status back to 'active' without one would hide the
    paused banner while the WorkflowRun stayed 'awaiting_human' underneath,
    leaving the human with no visible way back to it."""
    rivulet = await _get_rivulet_or_404(db, rivulet_id)
    if await find_awaiting_workflow_run(db, rivulet_id) is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A workflow is waiting on a reply here — reply with a message instead of resuming",
        )
    rivulet.status = "active"
    guard_state = await get_or_create_guard_state(db, rivulet_id)
    reset_guard_state(guard_state)
    await db.commit()
    await db.refresh(rivulet)
    await _publish_rivulet_change(db, rivulet)
    return rivulet


@router.delete("/rivulets/{rivulet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_rivulet(rivulet_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    rivulet = await _get_rivulet_or_404(db, rivulet_id)
    rivulet.status = "closed"
    await db.commit()
    await _publish_rivulet_change(db, rivulet)


@router.get("/rivulets/{rivulet_id}/stream")
async def stream_rivulet(
    rivulet_id: str, request: Request, db: DbSession, claims: CurrentStreamClaims
) -> StreamingResponse:
    """SSE endpoint (api-design.md#sse-protocol), backed by streaming.py's
    in-process pub/sub. Stays open for the life of the connection — a
    client viewing a rivulet gets every dispatch round's events as they
    happen, not just the first one, so this never sends a terminal event
    of its own; the generator only exits on client disconnect.

    Emits agent_status, agent_token, agent_message, handoff, system_alert,
    error, and done (dispatch/service.py). agent_status (#30) covers an
    agent's "thinking" / "executing_tool" / "waiting_for_handoff" states
    between invocation and its first streamed token or persisted message —
    tool calls get their status via that event's `detail` (tool name only,
    never args, per FR-5.5); the handoff tool specifically also still gets
    its own dedicated `handoff` event once the call completes.

    Not owner-gated — an invite-grant session opening this route is by
    design (it's how an invitee sees the conversation they were invited
    into). What's gated is per-event: subscribe() records this session's
    grant so streaming.py's publish() can skip owner_only events (#286,
    e.g. a freshly created invite's one-shot URL) for anyone who isn't the
    owner, without needing a separate channel or route.

    The DB session this pulls in via dependency injection stays open for
    as long as the connection does, same as the subscription — acceptable
    overhead for a local single-user SQLite-backed app, not worth the
    added complexity of releasing it early for one existence check.
    """
    await _get_rivulet_or_404(db, rivulet_id)
    queue = subscribe(rivulet_id, is_owner=claims.grant == "owner")

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
            unsubscribe(rivulet_id, queue)

    return StreamingResponse(event_source(), media_type="text/event-stream")
