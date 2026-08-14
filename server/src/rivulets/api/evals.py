"""Eval suites (#95): named sets of regression-test cases attached to
exactly one Agent or Workflow, each judged one of four ways (exact/
substring string match, an LLM-as-judge rubric, or -- agent suites only --
a structural check of which tool was called). Running a suite (`POST
.../run`) executes every case against its subject and returns a scored
report; see evals/runner.py and evals/judge.py for the execution/scoring
logic this module just exposes over HTTP.

Suite/case *definitions* sync across peers like Agent/Workflow (sync/
apply.py); EvalRun/EvalCaseResult (execution history) are read-only here,
local to whichever node actually ran them -- same split as Workflow/
WorkflowRun. Suite/case deletes call publish_tombstone (#287) so a peer
that still has the row doesn't resurrect it on its next edit -- delete_case
needs its own tombstone since a lone case delete leaves its parent suite
in place, but delete_suite's cascade-deleted EvalCase children don't:
EvalCase.suite_id has a real ondelete='CASCADE' FK (db/models.py), so a
peer applying the suite's tombstone has its own case rows cascade-deleted
at the SQLite level too.

`structural` judging is rejected for a workflow-attached suite (create_case
and update_case) since only an agent run's RunOutput.tools carries
tool-call data -- WorkflowRun has no equivalent (evals/runner.py's
_run_workflow_case always returns an empty tool-call list).
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from rivulets.api.agents import agent_holds_owner_scope
from rivulets.api.deps import (
    CurrentHumanId,
    CurrentWorkspaceId,
    DbSession,
    SessionClaims,
    get_session_claims,
)
from rivulets.api.workflows import workflow_holds_owner_scoped_agent
from rivulets.db.models import Agent, EvalCase, EvalCaseResult, EvalRun, EvalSuite, Workflow
from rivulets.evals import run_eval_suite
from rivulets.sync.publish import publish_current_state, publish_tombstone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evals", tags=["evals"])

_JUDGE_TYPES = ("exact", "substring", "llm_judge", "structural")


class EvalSuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    agent_id: str | None = None
    workflow_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> "EvalSuiteCreate":
        if (self.agent_id is None) == (self.workflow_id is None):
            raise ValueError("Exactly one of agent_id or workflow_id must be set")
        return self


class EvalSuiteUpdate(BaseModel):
    # agent_id/workflow_id are not patchable -- re-parenting a suite could
    # silently invalidate a 'structural' case if moved onto a workflow;
    # delete and recreate instead, same as this codebase has no precedent
    # for re-parenting any other synced child entity either.
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class EvalSuiteOut(BaseModel):
    id: str
    name: str
    description: str | None
    agent_id: str | None
    workflow_id: str | None
    subject_type: str
    subject_name: str
    case_count: int
    created_at: str
    updated_at: str


class EvalCaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    input_content: str = Field(min_length=1)
    judge_type: str
    expected_output: str | None = None
    rubric: str | None = None
    expected_tool_name: str | None = None
    expected_tool_args: dict[str, object] | None = None


class EvalCaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    input_content: str | None = Field(default=None, min_length=1)
    judge_type: str | None = None
    expected_output: str | None = None
    rubric: str | None = None
    expected_tool_name: str | None = None
    expected_tool_args: dict[str, object] | None = None


class EvalCaseOut(BaseModel):
    id: str
    suite_id: str
    name: str
    input_content: str
    judge_type: str
    expected_output: str | None
    rubric: str | None
    expected_tool_name: str | None
    expected_tool_args: dict[str, object] | None

    @classmethod
    def from_row(cls, row: EvalCase) -> "EvalCaseOut":
        return cls(
            id=row.id,
            suite_id=row.suite_id,
            name=row.name,
            input_content=row.input_content,
            judge_type=row.judge_type,
            expected_output=row.expected_output,
            rubric=row.rubric,
            expected_tool_name=row.expected_tool_name,
            expected_tool_args=(
                json.loads(row.expected_tool_args_json) if row.expected_tool_args_json else None
            ),
        )


class EvalRunOut(BaseModel):
    id: str
    suite_id: str
    status: str
    triggered_by: str
    triggered_by_id: str | None
    case_count: int
    pass_count: int
    fail_count: int
    error_count: int
    started_at: str
    completed_at: str | None

    model_config = {"from_attributes": True}


class EvalCaseResultOut(BaseModel):
    id: str
    run_id: str
    case_id: str
    status: str
    score: float | None
    actual_output: str | None
    actual_tool_calls: list[dict[str, object]] | None
    judge_reasoning: str | None
    error_message: str | None
    started_at: str
    completed_at: str | None

    @classmethod
    def from_row(cls, row: EvalCaseResult) -> "EvalCaseResultOut":
        return cls(
            id=row.id,
            run_id=row.run_id,
            case_id=row.case_id,
            status=row.status,
            score=row.score,
            actual_output=row.actual_output,
            actual_tool_calls=(
                json.loads(row.actual_tool_calls_json) if row.actual_tool_calls_json else None
            ),
            judge_reasoning=row.judge_reasoning,
            error_message=row.error_message,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )


async def _require_owner_for_scoped_subject(
    db: DbSession, claims: SessionClaims, *, agent_id: str | None, workflow_id: str | None
) -> None:
    """#326: an eval suite executes its subject directly (evals/runner.py's
    run_eval_suite -> run_agent/run_workflow), bypassing both the workflow
    published gate (find_workflow_by_name) and channel dispatch entirely --
    the same confused-deputy reach as attaching a scoped agent to a team
    (teams.py) or a workflow node (workflows.py), just via a third
    invocation path. Checked on both create (fail fast) and run (the
    authoritative check -- dispatch honors a scope grant made *after* the
    suite was created, same as agent_holds_owner_scope's own docstring)."""
    if claims.grant == "owner":
        return
    if agent_id is not None and await agent_holds_owner_scope(db, agent_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Owner access required to run an eval suite against an agent that holds a "
            "capability scope",
        )
    if workflow_id is not None and await workflow_holds_owner_scoped_agent(db, workflow_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Owner access required to run an eval suite against a workflow with a "
            "capability-scoped agent node",
        )


async def _get_suite_or_404(db: DbSession, suite_id: str) -> EvalSuite:
    suite = await db.get(EvalSuite, suite_id)
    if suite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eval suite not found")
    return suite


async def _get_case_or_404(db: DbSession, suite_id: str, case_id: str) -> EvalCase:
    case = await db.get(EvalCase, case_id)
    if case is None or case.suite_id != suite_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eval case not found")
    return case


async def _suite_out(db: DbSession, suite: EvalSuite) -> EvalSuiteOut:
    if suite.agent_id is not None:
        agent = await db.get(Agent, suite.agent_id)
        subject_type, subject_name = "agent", agent.name if agent is not None else "(deleted agent)"
    else:
        assert suite.workflow_id is not None
        workflow = await db.get(Workflow, suite.workflow_id)
        subject_type = "workflow"
        subject_name = workflow.name if workflow is not None else "(deleted workflow)"
    case_count = await db.scalar(
        select(func.count()).select_from(EvalCase).where(EvalCase.suite_id == suite.id)
    )
    return EvalSuiteOut(
        id=suite.id,
        name=suite.name,
        description=suite.description,
        agent_id=suite.agent_id,
        workflow_id=suite.workflow_id,
        subject_type=subject_type,
        subject_name=subject_name,
        case_count=case_count or 0,
        created_at=suite.created_at,
        updated_at=suite.updated_at,
    )


def _validate_case_fields(
    judge_type: str,
    expected_output: str | None,
    rubric: str | None,
    expected_tool_name: str | None,
) -> None:
    if judge_type not in _JUDGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown judge_type: {judge_type!r}")
    if judge_type in ("exact", "substring") and not expected_output:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"judge_type={judge_type!r} requires expected_output"
        )
    if judge_type == "llm_judge" and not rubric:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "judge_type='llm_judge' requires a rubric")
    if judge_type == "structural" and not expected_tool_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "judge_type='structural' requires expected_tool_name"
        )


@router.post("/suites", response_model=EvalSuiteOut, status_code=status.HTTP_201_CREATED)
async def create_suite(
    body: EvalSuiteCreate,
    db: DbSession,
    _: CurrentWorkspaceId,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> EvalSuiteOut:
    if body.agent_id is not None and await db.get(Agent, body.agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if body.workflow_id is not None and await db.get(Workflow, body.workflow_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    await _require_owner_for_scoped_subject(
        db, claims, agent_id=body.agent_id, workflow_id=body.workflow_id
    )

    suite = EvalSuite(
        name=body.name,
        description=body.description,
        agent_id=body.agent_id,
        workflow_id=body.workflow_id,
    )
    db.add(suite)
    await db.commit()
    await db.refresh(suite)
    await publish_current_state(db, "eval_suite", suite.id)
    return await _suite_out(db, suite)


@router.get("/suites", response_model=list[EvalSuiteOut])
async def list_suites(db: DbSession, _: CurrentWorkspaceId) -> list[EvalSuiteOut]:
    result = await db.execute(select(EvalSuite).order_by(EvalSuite.name))
    return [await _suite_out(db, suite) for suite in result.scalars().all()]


@router.get("/suites/{suite_id}", response_model=EvalSuiteOut)
async def get_suite(suite_id: str, db: DbSession, _: CurrentWorkspaceId) -> EvalSuiteOut:
    suite = await _get_suite_or_404(db, suite_id)
    return await _suite_out(db, suite)


@router.patch("/suites/{suite_id}", response_model=EvalSuiteOut)
async def update_suite(
    suite_id: str, body: EvalSuiteUpdate, db: DbSession, _: CurrentWorkspaceId
) -> EvalSuiteOut:
    suite = await _get_suite_or_404(db, suite_id)
    if body.name is not None:
        suite.name = body.name
    if body.description is not None:
        suite.description = body.description
    await db.commit()
    await db.refresh(suite)
    await publish_current_state(db, "eval_suite", suite.id)
    return await _suite_out(db, suite)


@router.delete("/suites/{suite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suite(suite_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    suite = await _get_suite_or_404(db, suite_id)
    await db.delete(suite)
    await db.commit()
    await publish_tombstone(db, "eval_suite", suite_id)


@router.post(
    "/suites/{suite_id}/cases", response_model=EvalCaseOut, status_code=status.HTTP_201_CREATED
)
async def create_case(
    suite_id: str, body: EvalCaseCreate, db: DbSession, _: CurrentWorkspaceId
) -> EvalCaseOut:
    suite = await _get_suite_or_404(db, suite_id)
    _validate_case_fields(
        body.judge_type, body.expected_output, body.rubric, body.expected_tool_name
    )
    if body.judge_type == "structural" and suite.workflow_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "structural judging is only supported for agent-attached suites",
        )

    case = EvalCase(
        suite_id=suite_id,
        name=body.name,
        input_content=body.input_content,
        judge_type=body.judge_type,
        expected_output=body.expected_output,
        rubric=body.rubric,
        expected_tool_name=body.expected_tool_name,
        expected_tool_args_json=(
            json.dumps(body.expected_tool_args) if body.expected_tool_args is not None else None
        ),
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    await publish_current_state(db, "eval_case", case.id)
    return EvalCaseOut.from_row(case)


@router.get("/suites/{suite_id}/cases", response_model=list[EvalCaseOut])
async def list_cases(suite_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[EvalCaseOut]:
    await _get_suite_or_404(db, suite_id)
    result = await db.execute(
        select(EvalCase).where(EvalCase.suite_id == suite_id).order_by(EvalCase.created_at)
    )
    return [EvalCaseOut.from_row(row) for row in result.scalars().all()]


@router.patch("/suites/{suite_id}/cases/{case_id}", response_model=EvalCaseOut)
async def update_case(
    suite_id: str, case_id: str, body: EvalCaseUpdate, db: DbSession, _: CurrentWorkspaceId
) -> EvalCaseOut:
    suite = await _get_suite_or_404(db, suite_id)
    case = await _get_case_or_404(db, suite_id, case_id)

    judge_type = body.judge_type if body.judge_type is not None else case.judge_type
    expected_output = (
        body.expected_output if body.expected_output is not None else case.expected_output
    )
    rubric = body.rubric if body.rubric is not None else case.rubric
    expected_tool_name = (
        body.expected_tool_name if body.expected_tool_name is not None else case.expected_tool_name
    )
    _validate_case_fields(judge_type, expected_output, rubric, expected_tool_name)
    if judge_type == "structural" and suite.workflow_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "structural judging is only supported for agent-attached suites",
        )

    if body.name is not None:
        case.name = body.name
    if body.input_content is not None:
        case.input_content = body.input_content
    case.judge_type = judge_type
    case.expected_output = expected_output
    case.rubric = rubric
    case.expected_tool_name = expected_tool_name
    if body.expected_tool_args is not None:
        case.expected_tool_args_json = json.dumps(body.expected_tool_args)

    await db.commit()
    await db.refresh(case)
    await publish_current_state(db, "eval_case", case.id)
    return EvalCaseOut.from_row(case)


@router.delete("/suites/{suite_id}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(suite_id: str, case_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    case = await _get_case_or_404(db, suite_id, case_id)
    await db.delete(case)
    await db.commit()
    await publish_tombstone(db, "eval_case", case_id)


@router.post("/suites/{suite_id}/run", response_model=EvalRunOut)
async def run_suite(
    suite_id: str,
    db: DbSession,
    _: CurrentWorkspaceId,
    human_id: CurrentHumanId,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> EvalRun:
    """Runs synchronously within this request -- on-demand-only is the
    confirmed v1 scope, and no background-job infrastructure exists
    elsewhere in this codebase to reuse for a queued alternative."""
    suite = await _get_suite_or_404(db, suite_id)
    # #326: the authoritative check -- see _require_owner_for_scoped_subject's
    # docstring for why this is re-checked here rather than only at
    # create_suite time.
    await _require_owner_for_scoped_subject(
        db, claims, agent_id=suite.agent_id, workflow_id=suite.workflow_id
    )
    case_count = await db.scalar(
        select(func.count()).select_from(EvalCase).where(EvalCase.suite_id == suite_id)
    )
    if not case_count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eval suite has no cases to run")
    return await run_eval_suite(db, suite, human_id=human_id)


@router.get("/suites/{suite_id}/runs", response_model=list[EvalRunOut])
async def list_runs(suite_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[EvalRun]:
    await _get_suite_or_404(db, suite_id)
    result = await db.execute(
        select(EvalRun)
        .where(EvalRun.suite_id == suite_id)
        .order_by(EvalRun.started_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())


@router.get("/suites/{suite_id}/runs/{run_id}/results", response_model=list[EvalCaseResultOut])
async def list_results(
    suite_id: str, run_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[EvalCaseResultOut]:
    run = await db.get(EvalRun, run_id)
    if run is None or run.suite_id != suite_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eval run not found")
    result = await db.execute(
        select(EvalCaseResult)
        .where(EvalCaseResult.run_id == run_id)
        .order_by(EvalCaseResult.started_at)
    )
    return [EvalCaseResultOut.from_row(row) for row in result.scalars().all()]
