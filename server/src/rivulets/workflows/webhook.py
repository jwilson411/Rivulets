"""Inbound HTTP triggering of a published workflow (#99).

api/webhooks.py's trigger endpoint verifies the request's HMAC signature
and that the target Workflow is published, then calls fire_webhook here
to actually run it -- the same split workflows/scheduler.py's
_fire/_fire_published_workflow keep, and the same Rivulet/kickoff-Message/
trace/run_workflow shape, with 'webhook' in place of 'schedule' as the
trigger identity.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.base import utcnow_iso
from rivulets.db.models import Channel, Message, Rivulet, Workflow, WorkflowRun, WorkflowWebhook
from rivulets.sync.publish import publish_current_state
from rivulets.tracing import finish_trace, start_trace
from rivulets.workflows.engine import run_workflow

logger = logging.getLogger(__name__)

# A webhook payload becomes input_content (directly, or via
# input_template) -- capped for the same reason http_request.py's
# _MAX_RESPONSE_CHARS is: this ends up in a Message and in an agent's
# context, not just logged.
MAX_INPUT_CONTENT_CHARS = 20_000


def build_input_content(webhook: WorkflowWebhook, raw_body: bytes) -> str:
    body_text = raw_body.decode("utf-8", errors="replace")
    content = (
        body_text
        if not webhook.input_template
        else webhook.input_template.replace("{input}", body_text)
    )
    return content[:MAX_INPUT_CONTENT_CHARS]


async def fire_webhook(
    db: AsyncSession,
    webhook: WorkflowWebhook,
    workflow: Workflow,
    raw_body: bytes,
    *,
    run_id: str | None = None,
) -> WorkflowRun:
    """Caller has already verified the HMAC signature and that
    `workflow.published` -- this only does the firing.

    `run_id` (#242): passed through to run_workflow so api/webhooks.py can
    hand this call off to a BackgroundTask while still committing to a
    run id in its already-sent 202 response -- see run_workflow's own
    docstring."""
    channel = await db.get(Channel, webhook.channel_id)
    if channel is None:
        raise RuntimeError(f"Webhook {webhook.id}'s target channel no longer exists")

    input_content = build_input_content(webhook, raw_body)

    rivulet = Rivulet(channel_id=webhook.channel_id, created_by=webhook.id)
    db.add(rivulet)
    await db.flush()
    kickoff = Message(
        rivulet_id=rivulet.id,
        sender_type="system",
        sender_name="system",
        content=f"Webhook trigger: /{workflow.name} {input_content}".rstrip(),
        content_type="system_alert",
    )
    db.add(kickoff)
    webhook.last_triggered_at = utcnow_iso()
    await db.commit()
    await db.refresh(rivulet)
    await publish_current_state(db, "rivulet", rivulet.id)
    await publish_current_state(db, "message", kickoff.id)

    trace_ctx = await start_trace(
        db,
        trigger_type="webhook",
        label=f"/{workflow.name}",
        rivulet_id=rivulet.id,
        channel_id=channel.id,
    )
    run = await run_workflow(
        db,
        workflow,
        rivulet,
        input_content,
        triggered_by="webhook",
        triggered_by_id=webhook.id,
        trace_ctx=trace_ctx,
        run_id=run_id,
    )
    await finish_trace(db, trace_ctx.trace_id)
    return run
