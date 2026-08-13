"""#99: HMAC-with-timestamp signing/verification (security/webhook_signing.py)
in isolation from the HTTP layer -- see test_workflow_webhooks_api.py for
the endpoint-level integration test.
"""

import time

import pytest

from rivulets.security.webhook_signing import ReplayGuard, sign, verify


def test_valid_signature_verifies() -> None:
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
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
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
    timestamp = str(int(time.time()))
    signature = sign(secret, timestamp, b"original")
    assert verify(secret, timestamp, b"tampered", signature) is False


def test_tampered_timestamp_fails() -> None:
    """The timestamp is part of the signed payload, not just carried
    alongside it -- forging a fresher timestamp on a captured signature
    must not verify, or replay protection would be meaningless."""
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
    body = b"payload"
    original_ts = str(int(time.time()))
    signature = sign(secret, original_ts, body)
    forged_ts = str(int(time.time()) + 1)
    assert verify(secret, forged_ts, body, signature) is False


def test_expired_timestamp_fails_even_with_correct_signature() -> None:
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
    stale_ts = str(int(time.time()) - 600)  # 10 minutes old, past the 300s default
    body = b"payload"
    signature = sign(secret, stale_ts, body)
    assert verify(secret, stale_ts, body, signature) is False


def test_expired_timestamp_within_custom_max_age_passes() -> None:
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
    stale_ts = str(int(time.time()) - 600)
    body = b"payload"
    signature = sign(secret, stale_ts, body)
    assert verify(secret, stale_ts, body, signature, max_age_seconds=700) is True


def test_malformed_timestamp_fails_without_raising() -> None:
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
    body = b"payload"
    signature = sign(secret, "not-a-number", body)
    assert verify(secret, "not-a-number", body, signature) is False


def test_malformed_signature_fails_without_raising() -> None:
    """#242: hmac.compare_digest can raise on some malformed inputs
    (non-ASCII in particular) instead of returning False -- verify() must
    still resolve to a plain False, not propagate an exception, so the
    one caller (api/webhooks.py) never turns this into a 500."""
    secret = "shh"  # noqa: S105 -- test fixture, not a real credential
    timestamp = str(int(time.time()))
    body = b"payload"
    assert verify(secret, timestamp, body, "too-short") is False
    assert verify(secret, timestamp, body, "g" * 64) is False  # right length, non-hex
    assert verify(secret, timestamp, body, "ñ" * 64) is False  # non-ASCII


def test_replay_guard_rejects_second_use_of_the_same_triple() -> None:
    guard = ReplayGuard()
    assert guard.check_and_record("webhook-1", "1000", "sig") is True
    assert guard.check_and_record("webhook-1", "1000", "sig") is False


def test_replay_guard_treats_a_different_signature_as_a_new_delivery() -> None:
    """Distinct signatures for the same webhook/timestamp are two
    genuinely different deliveries (e.g. a rotated secret), not a
    replay -- only an exact (webhook_id, timestamp, signature) match is
    rejected."""
    guard = ReplayGuard()
    assert guard.check_and_record("webhook-1", "1000", "sig-a") is True
    assert guard.check_and_record("webhook-1", "1000", "sig-b") is True


def test_replay_guard_expires_entries_past_max_age(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pruning is based on `max_age_seconds`, not an unbounded memory of
    every delivery ever seen -- once an entry ages out, the same triple
    is treated as a fresh delivery again."""
    clock = [1_000.0]
    monkeypatch.setattr("rivulets.security.webhook_signing.time.time", lambda: clock[0])
    guard = ReplayGuard(max_age_seconds=300)
    assert guard.check_and_record("webhook-1", "1000", "sig") is True
    clock[0] += 301
    assert guard.check_and_record("webhook-1", "1000", "sig") is True
