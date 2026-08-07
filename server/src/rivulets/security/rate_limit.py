"""In-process login rate limiter (security-and-dr.md: "Rate limiting on
login endpoint: 5 attempts per minute per IP (mitigates brute force)" —
documented since this file didn't exist, so nothing enforced it).

A single in-memory sliding window keyed by client IP is enough: the App
Server is a single process bound to 127.0.0.1 only (NFR-3.4), so there's
no multi-instance/load-balancer scenario that would need shared state
across processes.

Counts every login attempt toward the cap, not just failures — an
attacker guessing mnemonics doesn't announce which attempt will succeed,
so gating only on failure would let a flood of guesses through right up
until (and including) the one that works.
"""

import threading
import time

_WINDOW_SECONDS = 60.0
_MAX_ATTEMPTS = 5


class LoginRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def check(self, client_ip: str) -> bool:
        """Records this attempt and returns whether it's allowed to
        proceed. Call before doing any credential validation, so a
        flood of guesses is capped regardless of whether any of them
        happen to be right."""
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._attempts.get(client_ip, []) if now - t < _WINDOW_SECONDS]
            if len(recent) >= _MAX_ATTEMPTS:
                self._attempts[client_ip] = recent
                return False
            recent.append(now)
            self._attempts[client_ip] = recent
            return True

    def reset_for_testing(self) -> None:
        with self._lock:
            self._attempts.clear()


_limiter = LoginRateLimiter()


def get_login_rate_limiter() -> LoginRateLimiter:
    return _limiter


# Same 5/minute/IP budget, same brute-force reasoning, but a separate
# counter (#15's invite-secret guessing shouldn't share a budget with
# mnemonic-guessing, and vice versa) -- an attacker who exhausts one
# shouldn't be locked out of legitimately retrying the other.
_invite_accept_limiter = LoginRateLimiter()


def get_invite_accept_rate_limiter() -> LoginRateLimiter:
    return _invite_accept_limiter
