"""#99: HMAC-with-timestamp signing/verification (security/webhook_signing.py)
in isolation from the HTTP layer -- see test_workflow_webhooks_api.py for
the endpoint-level integration test.
"""

import time

from rivulets.security.webhook_signing import sign, verify


def test_valid_signature_verifies() -> None:
    secret = "shh"  # noqa: S105
    timestamp = str(int(time.time()))
    body = b'{"event": "push"}'
    signature = sign(secret, timestamp, body)
    assert verify(secret, timestamp, body, signature) is True


def test_wrong_secret_fails() -> None:
    timestamp = str(int(time.time()))
    body = b"payload"
    signature = sign("real-secret", timestamp, body)
    assert verify("wrong-secret", timestamp, body, signature) is False


def test_tampered_body_fails() -> None:
    secret = "shh"  # noqa: S105
    timestamp = str(int(time.time()))
    signature = sign(secret, timestamp, b"original")
    assert verify(secret, timestamp, b"tampered", signature) is False


def test_tampered_timestamp_fails() -> None:
    """The timestamp is part of the signed payload, not just carried
    alongside it -- forging a fresher timestamp on a captured signature
    must not verify, or replay protection would be meaningless."""
    secret = "shh"  # noqa: S105
    body = b"payload"
    original_ts = str(int(time.time()))
    signature = sign(secret, original_ts, body)
    forged_ts = str(int(time.time()) + 1)
    assert verify(secret, forged_ts, body, signature) is False


def test_expired_timestamp_fails_even_with_correct_signature() -> None:
    secret = "shh"  # noqa: S105
    stale_ts = str(int(time.time()) - 600)  # 10 minutes old, past the 300s default
    body = b"payload"
    signature = sign(secret, stale_ts, body)
    assert verify(secret, stale_ts, body, signature) is False


def test_expired_timestamp_within_custom_max_age_passes() -> None:
    secret = "shh"  # noqa: S105
    stale_ts = str(int(time.time()) - 600)
    body = b"payload"
    signature = sign(secret, stale_ts, body)
    assert verify(secret, stale_ts, body, signature, max_age_seconds=700) is True


def test_malformed_timestamp_fails_without_raising() -> None:
    secret = "shh"  # noqa: S105
    body = b"payload"
    signature = sign(secret, "not-a-number", body)
    assert verify(secret, "not-a-number", body, signature) is False
