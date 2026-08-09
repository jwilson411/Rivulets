"""Unit coverage for #103's fallback-chain building blocks in
dispatch/service.py: the retryable-vs-not error classifier and the
Agent.fallback_models JSON parser. The end-to-end retry behavior (actually
falling back mid-dispatch) is covered by test_rivulet_dispatch.py's
test_fallback_used_when_primary_hits_retryable_error and
test_fallback_not_used_for_non_retryable_error.
"""

from rivulets.db.models import Agent
from rivulets.dispatch.service import (
    _fallback_candidates,  # pyright: ignore[reportPrivateUsage]
    _is_retryable_error,  # pyright: ignore[reportPrivateUsage]
)


def test_rate_limit_is_retryable() -> None:
    assert _is_retryable_error("Error code: 429 - rate limit exceeded")


def test_server_errors_are_retryable() -> None:
    assert _is_retryable_error("Error code: 500 - internal server error")
    assert _is_retryable_error("Error code: 503 - service unavailable")


def test_auth_error_is_not_retryable() -> None:
    assert not _is_retryable_error("Error code: 401 - invalid x-api-key")
    assert not _is_retryable_error("Error code: 403 - forbidden")


def test_bad_request_is_not_retryable() -> None:
    assert not _is_retryable_error("Error code: 400 - invalid request")
    assert not _is_retryable_error("Error code: 422 - unprocessable entity")


def test_connection_error_without_status_code_is_retryable() -> None:
    assert _is_retryable_error("Connection to provider timed out")
    assert _is_retryable_error("provider unreachable")


def test_unrecognized_error_without_status_code_or_keyword_is_not_retryable() -> None:
    assert not _is_retryable_error("the model refused: content policy violation")


def test_fallback_candidates_parses_json_list() -> None:
    agent = Agent(
        id="a1",
        name="Test",
        description="x" * 10,
        instructions="x",
        model="anthropic:claude-3-5-haiku-latest",
        fallback_models='["openai:gpt-4o-mini", "deepseek:deepseek-chat"]',
    )
    assert _fallback_candidates(agent) == ["openai:gpt-4o-mini", "deepseek:deepseek-chat"]


def test_fallback_candidates_empty_when_unset() -> None:
    agent = Agent(
        id="a1",
        name="Test",
        description="x" * 10,
        instructions="x",
        model="anthropic:claude-3-5-haiku-latest",
        fallback_models=None,
    )
    assert _fallback_candidates(agent) == []


def test_fallback_candidates_ignores_malformed_json() -> None:
    agent = Agent(
        id="a1",
        name="Test",
        description="x" * 10,
        instructions="x",
        model="anthropic:claude-3-5-haiku-latest",
        fallback_models="not json",
    )
    assert _fallback_candidates(agent) == []


def test_fallback_candidates_filters_auto_sentinel() -> None:
    agent = Agent(
        id="a1",
        name="Test",
        description="x" * 10,
        instructions="x",
        model="anthropic:claude-3-5-haiku-latest",
        fallback_models='["auto", "openai:gpt-4o-mini"]',
    )
    assert _fallback_candidates(agent) == ["openai:gpt-4o-mini"]
