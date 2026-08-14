"""Inbound webhook triggering for workflows (#99): an external system
(GitHub, Stripe, a monitoring alert, ...) POSTs here to start a published
workflow, the same complementary role api/workflows.py's cron schedules
(#92) and workflows/engine.py's failure remediation (#94) play for
time-based and internally-triggered runs, respectively -- this is the
externally-triggered case.

POST /webhooks/{webhook_id} is deliberately not CurrentWorkspaceId-gated
-- same reasoning as api/invites.py's accept_invite: the request's HMAC
signature (security/webhook_signing.py) *is* the credential being
presented here, not a bearer token the caller doesn't have. CRUD for
WorkflowWebhook itself (create/list/update/delete/rotate-secret) lives in
api/workflows.py alongside the schedule CRUD it mirrors -- create/rotate/
delete are additionally owner-gated there (#242: they mint or destroy the
HMAC secret that *is* the credential this route accepts, same bucket as
invite management) -- only this one trigger route needs to be reachable
without a session at all.

Reachability is the caller's own responsibility, same limitation #121
already documents for invite links: this node's HTTP port is loopback-only
by default (NFR-3.4), so an external sender can only reach this endpoint
if the human has deliberately exposed the app beyond loopback (Docker
`-p` to a real interface, a reverse proxy, Tailscale, ...). There's no
port-forwarding/tunneling mechanism in this codebase to solve that, so it
isn't attempted here either.

The signature/timestamp headers verify() checks aren't enough to stop a
replay on their own -- a validly-signed request stays replayable for the
whole `max_age_seconds` window otherwise -- so a verified request is also
checked against `security/webhook_signing.py`'s ReplayGuard before it's
allowed to fire (#242). And the actual firing (`fire_webhook` ->
`run_workflow`, potentially slow) runs as a BackgroundTask rather than
being awaited inline, so this route's advertised 202 Accepted is no
longer a lie: the response is sent as soon as the request is validated,
not after the workflow finishes.

The replay triple is recorded before that BackgroundTask runs (it has to
be, to stop a duplicate delivery arriving before the first has finished
firing from being dispatched twice) -- but the 202 that recording earns
isn't a promise the workflow actually ran, only that it's been handed off.
If the background fire then fails, `_fire_webhook_in_background` releases
the triple it consumed (#322): the sender's own at-least-once retry of
that exact signed delivery is what recovers, not this endpoint retrying
anything itself.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.api.deps import DbSession
from rivulets.db.base import uuid7
from rivulets.db.models import Workflow, WorkflowWebhook
from rivulets.security.rate_limit import get_webhook_trigger_rate_limiter
from rivulets.security.session import get_session_key_store
from rivulets.security.webhook_secret_store import decrypt_webhook_secret
from rivulets.security.webhook_signing import get_webhook_replay_guard, verify
from rivulets.workflows.webhook import fire_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# GitHub/Stripe-style headers: a signature over "{timestamp}.{body}" and
# the timestamp itself, so verify() can reject a replayed-but-validly-
# signed request that's too old (webhook_signing.py's module docstring).
_SIGNATURE_HEADER = "X-Rivulets-Signature"
_TIMESTAMP_HEADER = "X-Rivulets-Timestamp"

# Bounds the body read before it's ever handed to HMAC verification or
# decoded as text -- same posture as api/files.py's upload cap, sized for
# a webhook payload (JSON events) rather than a file.
_MAX_BODY_BYTES = 262_144  # 256 KiB


class WebhookTriggerResponse(BaseModel):
    run_id: str
    status: str


async def _fire_webhook_in_background(
    db: AsyncSession,
    webhook: WorkflowWebhook,
    workflow: Workflow,
    raw_body: bytes,
    run_id: str,
    timestamp: str,
    signature: str,
) -> None:
    """Runs as a BackgroundTask -- Starlette only starts this after the
    202 response above has already been sent, the same pattern
    api/update.py's _exit_after_response uses. This is what actually
    keeps a slow workflow off the request task: the sender gets its
    Accepted response as soon as the signature/replay checks pass, not
    after the workflow finishes running. Any failure here can only be
    logged, not turned into an HTTP error -- the sender's response is
    long gone by the time this runs. (#322) So instead of leaving that
    202 as the sender's only shot, a failure here releases the replay
    triple `trigger_webhook` already recorded: the sender's at-least-once
    retry of the exact same signed delivery is then treated as new
    rather than hitting a 409 for a run that never happened."""
    try:
        await fire_webhook(db, webhook, workflow, raw_body, run_id=run_id)
    except Exception:
        logger.warning(
            "Webhook %s failed to fire, releasing delivery for retry", webhook.id, exc_info=True
        )
        get_webhook_replay_guard().release(webhook.id, timestamp, signature)


@router.post(
    "/{webhook_id}",
    response_model=WebhookTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_webhook(
    webhook_id: str, request: Request, db: DbSession, background_tasks: BackgroundTasks
) -> WebhookTriggerResponse:
    """Deliberately not CurrentWorkspaceId-gated -- see module docstring."""
    client_ip = request.client.host if request.client else "unknown"
    if not get_webhook_trigger_rate_limiter().check(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests — try again shortly"
        )

    webhook = await db.get(WorkflowWebhook, webhook_id)
    if webhook is None or not webhook.enabled:
        # Same shape for "never existed" and "disabled" -- no need to let
        # an unauthenticated caller distinguish the two.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")

    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and content_length.isdigit()
        and int(content_length) > _MAX_BODY_BYTES
    ):
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large")
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large")

    timestamp = request.headers.get(_TIMESTAMP_HEADER)
    signature = request.headers.get(_SIGNATURE_HEADER)
    if not timestamp or not signature:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Missing {_TIMESTAMP_HEADER}/{_SIGNATURE_HEADER} headers",
        )

    try:
        encryption_key = get_session_key_store().get_webhook_secret_key()
    except RuntimeError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This workspace isn't currently unlocked on this node — open Rivulets here first",
        ) from exc
    secret = decrypt_webhook_secret(webhook.secret_nonce, webhook.secret_ciphertext, encryption_key)

    if not verify(secret, timestamp, raw_body, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    workflow = await db.get(Workflow, webhook.workflow_id)
    if workflow is None or not workflow.published:
        # Same gate every trigger path shares (workflows/trigger.py) --
        # a 409, not a 404: the webhook itself is real and correctly
        # authenticated, it's just not ready to fire yet. Checked before
        # the replay guard below records anything (#322) -- this delivery
        # was never going to fire, so it shouldn't burn the one shot a
        # sender's retry gets once the workflow *is* published.
        raise HTTPException(status.HTTP_409_CONFLICT, "This workflow isn't published")

    # #242: only reached once the signature's already verified, so an
    # unauthenticated caller can't fill this store with garbage triples --
    # a resend of the exact same signed delivery within the window (a
    # sender's at-least-once retry, or a captured-and-replayed request)
    # is rejected here rather than re-firing the workflow a second time.
    if not get_webhook_replay_guard().check_and_record(webhook_id, timestamp, signature):
        raise HTTPException(status.HTTP_409_CONFLICT, "This delivery has already been processed")

    # #242: the id is chosen here, before the run exists, so it can go in
    # the response below -- the run itself is created and executed by a
    # BackgroundTask (_fire_webhook_in_background), not on this request.
    run_id = uuid7()
    background_tasks.add_task(
        _fire_webhook_in_background, db, webhook, workflow, raw_body, run_id, timestamp, signature
    )
    return WebhookTriggerResponse(run_id=run_id, status="running")
