"""Scoring logic for the four EvalCase.judge_type values (#95).

The LLM-as-judge path follows dispatch/rule_generation.py's and
dispatch/llm_fallback.py's shared "cheap LLM call" idiom exactly: resolve a
cheap-tier model, isolate the actual network call behind a private
`_run_judge_generator` seam so tests can monkeypatch just that function,
and never raise — a judge that can't run becomes an 'error' verdict
(NFR-2.4), not an exception that would abort the whole suite run
(evals/runner.py catches per-case errors anyway, but the judge itself
degrading gracefully keeps that boundary in one place).

Deliberately resolves the 'cheap' tier via agentos.models.resolve_tier_model
rather than rule_generation.py's pick_dispatcher_model: pick_dispatcher_model
also consults the dispatcher-specific 'dispatcher.model_override' workspace
setting, and coupling eval-suite grading to a dispatch-routing-cost knob
would be the wrong abstraction. resolve_tier_model's 'model_tiers.override'
setting is the feature-agnostic "what does this workspace call cheap"
answer, already shared by Auto-mode agents (#23).
"""

import logging
from dataclasses import dataclass
from typing import Literal, TypedDict

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from agno.run.agent import RunOutput
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.models import resolve_model, resolve_tier_model

logger = logging.getLogger(__name__)

JudgeStatus = Literal["passed", "failed", "error"]


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    status: JudgeStatus
    score: float | None = None
    reasoning: str | None = None
    error_message: str | None = None


class ToolCallDict(TypedDict):
    tool_name: str
    tool_args: dict[str, object] | None


def judge_exact(expected: str, actual: str) -> JudgeVerdict:
    status: JudgeStatus = "passed" if actual.strip() == expected.strip() else "failed"
    return JudgeVerdict(status=status)


def judge_substring(expected: str, actual: str) -> JudgeVerdict:
    status: JudgeStatus = "passed" if expected.strip() in actual else "failed"
    return JudgeVerdict(status=status)


def judge_structural(
    expected_tool_name: str,
    expected_tool_args: dict[str, object] | None,
    tool_calls: list[ToolCallDict],
) -> JudgeVerdict:
    """Copies dispatch/service.py's `_find_handoff_call` idiom: scan the
    run's recorded tool calls for one matching `expected_tool_name`.

    An empty `tool_calls` list is 'error', not an automatic 'failed':
    run_agent's own docstring documents that some providers never emit the
    terminal RunCompletedEvent that carries `.tools`, even on a genuinely
    successful completion — so "no calls recorded" is inconclusive, not
    proof nothing was called.

    A match on tool name with `expected_tool_args` set does a *subset*
    match (every expected key present with an equal value), not full dict
    equality — a real call may carry extra incidental arguments the case
    author didn't think to pin down.
    """
    if not tool_calls:
        return JudgeVerdict(
            status="error",
            error_message=(
                "No tool calls were recorded for this run — some providers don't "
                "report them even on a successful completion, so this case can't "
                "be judged."
            ),
        )

    for call in tool_calls:
        if call["tool_name"] != expected_tool_name:
            continue
        if expected_tool_args is None:
            return JudgeVerdict(status="passed")
        actual_args = call["tool_args"] or {}
        if all(actual_args.get(key) == value for key, value in expected_tool_args.items()):
            return JudgeVerdict(status="passed")

    return JudgeVerdict(status="failed")


_JUDGE_INSTRUCTIONS = """You grade an AI system's output against a rubric for an
automated regression-test suite. Given the input given to the system, the rubric it
must satisfy, and the system's actual output, decide whether the output satisfies the
rubric. Score from 0.0 (completely fails the rubric) to 1.0 (fully satisfies it) --
use the full range, not just 0/0.5/1. Set passed=true only if the output would be
acceptable to ship, not merely "partially there". Give one or two sentences of
reasoning citing specifics from the actual output."""


class JudgeVerdictSchema(BaseModel):
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str


def _build_judge_prompt(case_input: str, rubric: str, actual_output: str) -> str:
    return (
        f"Input given to the system:\n{case_input}\n\n"
        f"Rubric:\n{rubric}\n\n"
        f"System's actual output:\n{actual_output}"
    )


async def _run_judge_generator(model: Model, prompt: str) -> RunOutput:
    """Isolated seam for tests to monkeypatch — same idiom as
    rule_generation.py's `_run_generator` / llm_fallback.py's
    `_run_decision`. Returns the raw RunOutput (not just the parsed
    verdict) so the caller can record its token/cost accounting (#354),
    same reasoning as `_run_decision`'s own docstring."""
    judge = AgnoAgent(model=model, instructions=_JUDGE_INSTRUCTIONS)
    return await judge.arun(  # pyright: ignore[reportUnknownMemberType]
        prompt, output_schema=JudgeVerdictSchema, stream=False
    )


async def judge_llm(
    db: AsyncSession, case_input: str, rubric: str, actual_output: str
) -> JudgeVerdict:
    """NFR-2.4-style graceful degradation, matching rule_generation.py/
    llm_fallback.py: no provider configured, or the call failing for any
    reason, becomes an 'error' verdict rather than raising.

    #354 (leftover of #320): this call burns a provider key same as any
    agent run, but never checked budget caps or recorded its spend. Now
    enforces caps first (workspace scope only: the judge grades on the
    suite's behalf, not as any Agent, so there's nothing more specific to
    attribute to -- same shape as the summarize node's check) and records
    the call via record_agent_run with agent_id=None, source='eval_judge'
    (#246's dispatcher_call precedent). A tripped hard_stop becomes an
    'error' verdict like every other way this judge can't run -- the
    call is refused (fails closed), and this function's never-raise
    contract keeps one blocked judge from aborting the whole suite run.

    Lazy import of dispatch.budgets: rivulets.dispatch's package init
    pulls in rivulets.api (via dispatch.service -> api.agents), whose own
    init imports this package back via api.evals -- same circular-import
    hazard evals/runner.py's _run_agent_case already works around the
    same way."""
    from rivulets.dispatch.budgets import BudgetCapBlockedError, enforce_budget_caps

    try:
        await enforce_budget_caps(db, None, None)
    except BudgetCapBlockedError as exc:
        return JudgeVerdict(status="error", error_message=str(exc))

    provider_model = await resolve_tier_model(db, "cheap")
    if provider_model is None:
        return JudgeVerdict(
            status="error", error_message="No provider configured for LLM-judge scoring"
        )

    try:
        model = await resolve_model(db, provider_model)
        run_output = await _run_judge_generator(
            model, _build_judge_prompt(case_input, rubric, actual_output)
        )
    except Exception as exc:
        logger.warning("LLM-judge call failed", exc_info=True)
        return JudgeVerdict(status="error", error_message=str(exc))

    # Recorded before the content is parsed -- the tokens were spent
    # whether or not the model produced usable structured output, same
    # ordering as llm_fallback.py's recording.
    await record_agent_run(
        db, None, provider_model, None, "completed", run_output, source="eval_judge"
    )

    content = run_output.content
    verdict = content if isinstance(content, JudgeVerdictSchema) else None
    if verdict is None:
        return JudgeVerdict(
            status="error", error_message="Judge model returned no structured output"
        )

    return JudgeVerdict(
        status="passed" if verdict.passed else "failed",
        score=verdict.score,
        reasoning=verdict.reasoning,
    )
