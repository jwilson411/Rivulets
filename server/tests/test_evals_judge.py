"""#95: pure scoring-logic coverage for the four EvalCase.judge_type
values (evals/judge.py). The LLM-as-judge tests follow test_rule_generation
.py's idiom exactly: monkeypatch the private `_run_judge_generator` seam,
never `arun` itself, and assert graceful ('error', not a raised exception)
degradation when no provider is configured or the call fails.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import AgentRun, BudgetCap, ProviderConfig
from rivulets.evals.judge import (
    JudgeVerdictSchema,
    ToolCallDict,
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
    calls: list[ToolCallDict] = [
        {"tool_name": "search", "tool_args": {"query": "cats", "limit": 5}}
    ]
    verdict = judge_structural("search", None, calls)
    assert verdict.status == "passed"


def test_judge_structural_passes_on_args_subset_match() -> None:
    calls: list[ToolCallDict] = [
        {"tool_name": "search", "tool_args": {"query": "cats", "limit": 5}}
    ]
    verdict = judge_structural("search", {"query": "cats"}, calls)
    assert verdict.status == "passed"


def test_judge_structural_fails_on_args_mismatch() -> None:
    calls: list[ToolCallDict] = [{"tool_name": "search", "tool_args": {"query": "dogs"}}]
    verdict = judge_structural("search", {"query": "cats"}, calls)
    assert verdict.status == "failed"


def test_judge_structural_fails_when_tool_not_called() -> None:
    calls: list[ToolCallDict] = [{"tool_name": "other_tool", "tool_args": None}]
    verdict = judge_structural("search", None, calls)
    assert verdict.status == "failed"


async def test_judge_llm_returns_error_with_no_provider_configured(
    db_session: AsyncSession,
) -> None:
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


def _fake_run_output(content: object) -> Any:
    """RunOutput-like double for the `_run_judge_generator` seam, which
    returns the raw RunOutput since #354 so judge_llm can record its
    spend -- `.metrics`/`.tools` deliberately absent, same duck-typing
    record_agent_run already tolerates from other tests' doubles."""
    return SimpleNamespace(content=content)


async def test_judge_llm_returns_error_when_output_is_unstructured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> Any:
        return _fake_run_output("plain text, not a JudgeVerdictSchema")

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

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> Any:
        return _fake_run_output(
            JudgeVerdictSchema(passed=True, score=0.9, reasoning="Covers the key points.")
        )

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

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> Any:
        return _fake_run_output(
            JudgeVerdictSchema(passed=False, score=0.1, reasoning="Misses the point entirely.")
        )

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.evals.judge._run_judge_generator", fake_run_judge_generator)

    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "failed"
    assert verdict.score == 0.1


async def test_judge_llm_is_blocked_by_a_tripped_workspace_hard_stop_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#354: the LLM-judge call is billed spend same as any agent run,
    but never checked budget caps before -- a tripped workspace hard_stop
    must refuse the call (fail closed) as an 'error' verdict, never
    invoking the model."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    db_session.add(
        BudgetCap(scope_type="workspace", period="day", limit_usd=0.5, action="hard_stop")
    )
    # Already over the $0.50 cap before the judge ever runs.
    db_session.add(
        AgentRun(model="anthropic:claude-3-5-haiku-latest", status="completed", cost_usd=1.0)
    )
    await db_session.commit()

    calls: list[str] = []

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def counting_generator(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return _fake_run_output(JudgeVerdictSchema(passed=True, score=1.0, reasoning="n/a"))

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.evals.judge._run_judge_generator", counting_generator)

    verdict = await judge_llm(db_session, "input", "rubric", "output")

    assert calls == []  # the model was never invoked
    assert verdict.status == "error"
    assert "budget cap" in (verdict.error_message or "").lower()
    assert "blocked" in (verdict.error_message or "").lower()


async def test_judge_llm_records_its_spend_as_an_agent_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#354: judge spend must count toward budget-cap windows and the
    usage dashboard -- recorded as an agent_id=None, source='eval_judge'
    row via the same record_agent_run choke point as every other billed
    call."""
    db_session.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db_session.commit()

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    async def fake_run_judge_generator(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            content=JudgeVerdictSchema(passed=True, score=1.0, reasoning="n/a"),
            metrics=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    monkeypatch.setattr("rivulets.evals.judge.resolve_model", fake_resolve_model)
    monkeypatch.setattr("rivulets.evals.judge._run_judge_generator", fake_run_judge_generator)

    verdict = await judge_llm(db_session, "input", "rubric", "output")
    assert verdict.status == "passed"

    run = (await db_session.scalars(select(AgentRun))).one()
    assert run.source == "eval_judge"
    assert run.agent_id is None
    assert run.total_tokens == 150
    # The default anthropic cheap-tier model is in pricing.py's table, so
    # this spend is priced (counts toward spend_usd, not unpriced_run_count).
    assert run.cost_usd is not None and run.cost_usd > 0
