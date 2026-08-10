"""#95: pure scoring-logic coverage for the four EvalCase.judge_type
values (evals/judge.py). The LLM-as-judge tests follow test_rule_generation
.py's idiom exactly: monkeypatch the private `_run_judge_generator` seam,
never `arun` itself, and assert graceful ('error', not a raised exception)
degradation when no provider is configured or the call fails.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import ProviderConfig
from rivulets.evals.judge import (
    JudgeVerdictSchema,
    judge_exact,
    judge_llm,
    judge_structural,
    judge_substring,
)


def test_judge_exact_passes_on_identical_strings() -> None:
    assert judge_exact("hello", "hello").status == "passed"


def test_judge_exact_ignores_surrounding_whitespace() -> None:
    assert judge_exact("hello", "  hello  ").status == "passed"


def test_judge_exact_fails_on_mismatch() -> None:
    verdict = judge_exact("hello", "goodbye")
    assert verdict.status == "failed"
    assert verdict.score is None


def test_judge_substring_passes_when_expected_is_contained() -> None:
    assert judge_substring("world", "hello world!").status == "passed"


def test_judge_substring_fails_when_absent() -> None:
    assert judge_substring("world", "hello there").status == "failed"


def test_judge_structural_empty_tool_calls_is_error_not_failed() -> None:
    verdict = judge_structural("search", None, [])
    assert verdict.status == "error"
    assert verdict.error_message is not None


def test_judge_structural_passes_when_tool_called_and_args_ignored() -> None:
    calls = [{"tool_name": "search", "tool_args": {"query": "cats", "limit": 5}}]
    verdict = judge_structural("search", None, calls)
    assert verdict.status == "passed"


def test_judge_structural_passes_on_args_subset_match() -> None:
    calls = [{"tool_name": "search", "tool_args": {"query": "cats", "limit": 5}}]
    verdict = judge_structural("search", {"query": "cats"}, calls)
    assert verdict.status == "passed"


def test_judge_structural_fails_on_args_mismatch() -> None:
    calls = [{"tool_name": "search", "tool_args": {"query": "dogs"}}]
    verdict = judge_structural("search", {"query": "cats"}, calls)
    assert verdict.status == "failed"


def test_judge_structural_fails_when_tool_not_called() -> None:
    calls = [{"tool_name": "other_tool", "tool_args": None}]
    verdict = judge_structural("search", None, calls)
    assert verdict.status == "failed"


async def test_judge_llm_returns_error_with_no_provider_configured(db_session: AsyncSession) -> None:
    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "error"
    assert verdict.error_message is not None


async def test_judge_llm_returns_error_when_resolve_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)

    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "error"
    assert "no keychain in CI" in (verdict.error_message or "")


async def test_judge_llm_returns_error_when_generator_returns_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.evals.judge._run_judge_generator", fake_run_judge_generator)

    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "error"


async def test_judge_llm_converts_generator_output_to_passed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> JudgeVerdictSchema:
        return JudgeVerdictSchema(passed=True, score=0.9, reasoning="Covers the key points.")

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.evals.judge._run_judge_generator", fake_run_judge_generator)

    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "passed"
    assert verdict.score == 0.9
    assert verdict.reasoning == "Covers the key points."


async def test_judge_llm_converts_generator_output_to_failed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> JudgeVerdictSchema:
        return JudgeVerdictSchema(passed=False, score=0.1, reasoning="Misses the point entirely.")

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.evals.judge._run_judge_generator", fake_run_judge_generator)

    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "failed"
    assert verdict.score == 0.1
