"""LoginRateLimiter (security/rate_limit.py) -- the sliding-window logic
itself, isolated from the HTTP layer (see test_auth.py for the
login-endpoint-level integration test).
"""

import pytest

from rivulets.security.rate_limit import LoginRateLimiter, get_webhook_trigger_rate_limiter


def test_allows_up_to_five_attempts_within_the_window() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        assert limiter.check("1.2.3.4") is True


def test_sixth_attempt_within_the_window_is_rejected() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") is False


def test_different_ips_have_independent_budgets() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") is False
    assert limiter.check("5.6.7.8") is True


def test_window_expiry_allows_further_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = LoginRateLimiter()
    current_time = 1000.0
    monkeypatch.setattr("rivulets.security.rate_limit.time.monotonic", lambda: current_time)

    for _ in range(5):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") is False

    current_time += 61.0  # past the 60s window
    assert limiter.check("1.2.3.4") is True


def test_reset_for_testing_clears_all_ips() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") is False

    limiter.reset_for_testing()

    assert limiter.check("1.2.3.4") is True


def test_custom_window_and_max_attempts_are_honored() -> None:
    """#99's webhook trigger limiter uses a wider budget than the default
    5/minute -- a signature-guessing attack is infeasible against a
    256-bit HMAC secret at any request rate, so this limiter's job is
    flood protection for a legitimately bursty sender, not brute-force
    mitigation."""
    limiter = LoginRateLimiter(window_seconds=60.0, max_attempts=30)
    for _ in range(30):
        assert limiter.check("1.2.3.4") is True
    assert limiter.check("1.2.3.4") is False


def test_webhook_trigger_limiter_has_independent_budget_from_login() -> None:
    get_webhook_trigger_rate_limiter().reset_for_testing()
    limiter = get_webhook_trigger_rate_limiter()
    for _ in range(30):
        assert limiter.check("9.9.9.9") is True
    assert limiter.check("9.9.9.9") is False
    limiter.reset_for_testing()
