"""HMAC-with-timestamp signing for inbound workflow webhooks (#99).

Mirrors the shape most webhook providers (GitHub, Stripe, ...) already
expect and validate on the sending side: a per-request timestamp plus an
HMAC-SHA256 over `{timestamp}.{body}`, compared with `hmac.compare_digest`
(constant-time) against the shared secret.

The timestamp is folded *into* the signed payload rather than just
carried alongside it, specifically so replay protection actually holds:
rejecting a stale timestamp is only meaningful because the signature
covers it -- if the timestamp weren't signed, a captured request could be
replayed by simply attaching a fresh one.
"""

import hashlib
import hmac
import time

DEFAULT_MAX_AGE_SECONDS = 300  # 5 minutes -- generous for clock drift/queueing


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(
        secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256
    )
    return mac.hexdigest()


def verify(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> bool:
    """False on any malformed input rather than raising, so the one
    caller (api/webhooks.py) can treat every failure mode -- bad
    timestamp, expired, wrong signature -- as a single uniform 401
    without a try/except at the call site."""
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - sent_at) > max_age_seconds:
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)
