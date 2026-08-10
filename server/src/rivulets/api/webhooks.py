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
api/workflows.py alongside the schedule CRUD it mirrors, gated normally
by CurrentWorkspaceId -- only this one trigger route needs to be reachable
without a session.

Reachability is the caller's own responsibility, same limitation #121
already documents for invite links: this node's HTTP port is loopback-only
by default (NFR-3.4), so an external sender can only reach this endpoint
if the human has deliberately exposed the app beyond loopback (Docker
`-p` to a real interface, a reverse proxy, Tailscale, ...). There's no
port-forwarding/tunneling mechanism in this codebase to solve that, so it
isn't attempted here either.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from rivulets.api.deps import DbSession
from rivulets.db.models import Workflow, WorkflowWebhook
from rivulets.security.rate_limit import get_webhook_trigger_rate_limiter
from rivulets.security.session import get_session_key_store
from rivulets.security.webhook_secret_store import decrypt_webhook_secret
from rivulets.security.webhook_signing import verify
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


@router.post(
    "/{webhook_id}",
    response_model=WebhookTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_webhook(webhook_id: str, request: Request, db: DbSession) -> WebhookTriggerResponse:
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
    if content_length is not None and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
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
        # authenticated, it's just not ready to fire yet.
        raise HTTPException(status.HTTP_409_CONFLICT, "This workflow isn't published")

    try:
        run = await fire_webhook(db, webhook, workflow, raw_body)
    except RuntimeError as exc:
        logger.warning("Webhook %s failed to fire: %s", webhook.id, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return WebhookTriggerResponse(run_id=run.id, status=run.status)
