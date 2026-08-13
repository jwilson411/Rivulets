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

`ReplayGuard` (#242) closes the remaining replay window `verify()` alone
can't: a validly-signed, not-yet-expired request can otherwise be resent
verbatim any number of times within `max_age_seconds` and re-fire the
workflow every time. It's an in-memory, single-process store -- same
posture as security/rate_limit.py's limiters (NFR-3.4: the App Server is
one process bound to loopback by default, so there's no multi-instance
scenario needing shared state), and the same reason it's fine for this to
not survive a restart: a webhook delivery a sender still considers
in-flight across a process restart is already outside the guarantees any
of this makes.
"""

import hashlib
import hmac
import re
import threading
import time

DEFAULT_MAX_AGE_SECONDS = 300  # 5 minutes -- generous for clock drift/queueing

# A signature is always our own hexdigest() output -- exactly 64 lowercase
# hex chars. Checked before it ever reaches compare_digest: some inputs
# (embedded non-ASCII bytes, in particular) make CPython's compare_digest
# raise instead of returning False, which would otherwise turn a malformed
# header on this unauthenticated route into a 500 rather than the uniform
# 401 every other failure mode here produces.
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
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
    timestamp, expired, malformed/wrong signature -- as a single uniform
    401 without a try/except at the call site."""
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - sent_at) > max_age_seconds:
        return False
    if not _SIGNATURE_RE.fullmatch(signature):
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


class ReplayGuard:
    """Tracks (webhook_id, timestamp, signature) triples already accepted
    by `verify()`, so the same valid delivery can't fire a second run --
    see module docstring."""

    def __init__(self, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> None:
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, str, str], float] = {}
        self._max_age_seconds = max_age_seconds

    def check_and_record(self, webhook_id: str, timestamp: str, signature: str) -> bool:
        """Returns True the first time this triple is seen (and records
        it); False on every subsequent call within `max_age_seconds` --
        the caller should only call this once a request has already
        passed `verify()`, so an unauthenticated caller can't fill this
        store with garbage."""
        key = (webhook_id, timestamp, signature)
        now = time.time()
        with self._lock:
            self._seen = {k: t for k, t in self._seen.items() if now - t <= self._max_age_seconds}
            if key in self._seen:
                return False
            self._seen[key] = now
            return True

    def reset_for_testing(self) -> None:
        with self._lock:
            self._seen.clear()


_replay_guard = ReplayGuard()


def get_webhook_replay_guard() -> ReplayGuard:
    return _replay_guard
