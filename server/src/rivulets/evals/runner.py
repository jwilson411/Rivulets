"""Executes an EvalSuite's cases against its Agent or Workflow subject and
persists the scored results (#95).

Runs entirely within the caller's request (api/evals.py's `run` route) --
on-demand-only is the confirmed v1 scope, and no background-job
infrastructure exists elsewhere in this codebase to reuse for a queued
alternative. One case failing to execute never aborts the run; it's
captured as that case's own 'error' EvalCaseResult (same "one thing
failing doesn't take down the batch" spirit as dispatch/service.py's
per-agent NFR-2.4 handling).
"""

import json
import logging

from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos import run_agent
from rivulets.db.base import utcnow_iso, uuid7
from rivulets.db.models import (
    Channel,
    EvalCase,
    EvalCaseResult,
    EvalRun,
    EvalSuite,
    Rivulet,
    Workflow,
)
from rivulets.evals.judge import (
    JudgeVerdict,
    ToolCallDict,
    judge_exact,
    judge_llm,
    judge_structural,
    judge_substring,
)
from rivulets.sync.publish import publish_current_state
from rivulets.workflows import run_workflow

logger = logging.getLogger(__name__)

# Name of the scratch channel eval-suite workflow runs execute against
# (evals require a real Rivulet -- see _run_workflow_case). Real,
# visible rows -- not a hidden/system channel, since no such flag exists
# in this schema; a documented v1 limitation, not a silent side effect.
_EVAL_CHANNEL_NAME = "_evals"


async def run_eval_suite(db: AsyncSession, suite: EvalSuite, *, human_id: str | None) -> EvalRun:
    """Runs every EvalCase in `suite` once (single-pass, no repeat/
    averaging in v1 -- see EvalRun's docstring), judges each against its
    own judge_type, and returns the completed, committed EvalRun."""
    cases = list(
        (
            await db.scalars(
                select(EvalCase).where(EvalCase.suite_id == suite.id).order_by(EvalCase.created_at)
            )
        ).all()
    )

    run = EvalRun(suite_id=suite.id, triggered_by_id=human_id, case_count=len(cases))
    db.add(run)
    await db.commit()
    await db.refresh(run)

    pass_count = fail_count = error_count = 0
    for case in cases:
        result = await _run_case(db, suite, case, run.id)
        db.add(result)
        await db.commit()
        if result.status == "passed":
            pass_count += 1
        elif result.status == "failed":
            fail_count += 1
        else:
            error_count += 1

    run.status = "completed"
    run.pass_count = pass_count
    run.fail_count = fail_count
    run.error_count = error_count
    run.completed_at = utcnow_iso()
    await db.commit()
    await db.refresh(run)
    return run


async def _run_case(db: AsyncSession, suite: EvalSuite, case: EvalCase, run_id: str) -> EvalCaseResult:
    started_at = utcnow_iso()
    try:
        if suite.agent_id is not None:
            actual_output, tool_calls = await _run_agent_case(db, suite.agent_id, case.input_content)
        else:
            assert suite.workflow_id is not None
            actual_output, tool_calls = await _run_workflow_case(
                db, suite.workflow_id, case.input_content, run_id
            )
    except Exception as exc:
        logger.warning("Eval case %r failed to execute", case.name, exc_info=True)
        return EvalCaseResult(
            run_id=run_id,
            case_id=case.id,
            status="error",
            error_message=str(exc),
            started_at=started_at,
            completed_at=utcnow_iso(),
        )

    verdict = await _judge(db, case, actual_output, tool_calls)
    return EvalCaseResult(
        run_id=run_id,
        case_id=case.id,
        status=verdict.status,
        score=verdict.score,
        actual_output=actual_output,
        actual_tool_calls_json=json.dumps(tool_calls) if case.judge_type == "structural" else None,
        judge_reasoning=verdict.reasoning,
        error_message=verdict.error_message,
        started_at=started_at,
        completed_at=utcnow_iso(),
    )


async def _run_agent_case(
    db: AsyncSession, agent_id: str, input_content: str
) -> tuple[str, list[ToolCallDict]]:
    """A fresh AgentOS session_id per case gives full isolation -- no
    Rivulet/Channel needed at all, unlike a workflow case."""
    session_id = uuid7()
    run_output = await run_agent(db, agent_id, input_content, session_id=session_id, user_id="eval")
    if run_output.status is RunStatus.error:
        raise RuntimeError(str(run_output.content))
    content = run_output.get_content_as_string() or ""  # pyright: ignore[reportUnknownMemberType]
    tool_calls: list[ToolCallDict] = [
        {"tool_name": t.tool_name, "tool_args": t.tool_args} for t in (run_output.tools or [])
    ]
    return content, tool_calls


async def _get_or_create_eval_channel(db: AsyncSession) -> Channel:
    existing = await db.scalar(select(Channel).where(Channel.name == _EVAL_CHANNEL_NAME))
    if existing is not None:
        return existing
    channel = Channel(
        name=_EVAL_CHANNEL_NAME,
        description="Scratch channel for eval-suite workflow runs (#95) -- not for human use.",
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    await publish_current_state(db, "channel", channel.id)
    return channel


async def _run_workflow_case(
    db: AsyncSession, workflow_id: str, input_content: str, run_id: str
) -> tuple[str, list[ToolCallDict]]:
    """Workflow execution requires a real Rivulet (workflows/engine.py's
    run_workflow posts Messages to it and reads its channel_id) -- a fresh
    one is created per case on a shared scratch channel, same isolation
    reasoning as _run_agent_case's fresh session_id."""
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None:
        raise RuntimeError("Workflow no longer exists")

    channel = await _get_or_create_eval_channel(db)
    rivulet = Rivulet(channel_id=channel.id, title=f"Eval run {run_id}", created_by="eval")
    db.add(rivulet)
    await db.commit()
    await db.refresh(rivulet)

    wf_run = await run_workflow(
        db, workflow, rivulet, input_content, triggered_by="eval", triggered_by_id=run_id
    )
    if wf_run.status == "failed":
        raise RuntimeError(wf_run.error_message or "Workflow run failed")
    if wf_run.status == "awaiting_human":
        raise RuntimeError("Workflow paused on a human_input node -- not supported in eval runs (v1)")
    # No tool-call data exists at the workflow-run level -- structural
    # judging is rejected for workflow-attached suites at the API layer.
    return wf_run.final_output or "", []


async def _judge(
    db: AsyncSession, case: EvalCase, actual_output: str, tool_calls: list[ToolCallDict]
) -> JudgeVerdict:
    if case.judge_type == "exact":
        return judge_exact(case.expected_output or "", actual_output)
    if case.judge_type == "substring":
        return judge_substring(case.expected_output or "", actual_output)
    if case.judge_type == "structural":
        expected_args = json.loads(case.expected_tool_args_json) if case.expected_tool_args_json else None
        return judge_structural(case.expected_tool_name or "", expected_args, tool_calls)
    return await judge_llm(db, case.input_content, case.rubric or "", actual_output)
