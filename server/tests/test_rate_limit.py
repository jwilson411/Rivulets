"""LoginRateLimiter (security/rate_limit.py) -- the sliding-window logic
itself, isolated from the HTTP layer (see test_auth.py for the
login-endpoint-level integration test).
"""

import pytest

from agent_hive.security.rate_limit import LoginRateLimiter


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
    monkeypatch.setattr("agent_hive.security.rate_limit.time.monotonic", lambda: current_time)

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
