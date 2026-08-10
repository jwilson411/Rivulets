"""#95: EvalSuite/EvalCase/EvalRun/EvalCaseResult schema-level behavior --
the single-subject CHECK constraint and cascade deletes, exercised
directly against a DB session (db_session fixture), mirroring how other
schema invariants (e.g. Workflow's entry-point uniqueness) get direct
coverage separate from the API layer.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Agent, EvalCase, EvalCaseResult, EvalRun, EvalSuite, Workflow


async def _make_agent(db: AsyncSession, name: str = "Grader") -> Agent:
    agent = Agent(
        name=name, description="d", instructions="i", model="anthropic:claude-haiku-4-5-20251001"
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_workflow(db: AsyncSession, name: str = "flow") -> Workflow:
    workflow = Workflow(name=name)
    db.add(workflow)
    await db.flush()
    return workflow


async def test_suite_requires_exactly_one_subject_rejects_neither(db_session: AsyncSession) -> None:
    db_session.add(EvalSuite(name="orphan"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_suite_requires_exactly_one_subject_rejects_both(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    workflow = await _make_workflow(db_session)
    db_session.add(EvalSuite(name="both", agent_id=agent.id, workflow_id=workflow.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_suite_accepts_agent_only_subject(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    db_session.add(EvalSuite(name="agent-suite", agent_id=agent.id))
    await db_session.commit()  # should not raise


async def test_suite_accepts_workflow_only_subject(db_session: AsyncSession) -> None:
    workflow = await _make_workflow(db_session)
    db_session.add(EvalSuite(name="workflow-suite", workflow_id=workflow.id))
    await db_session.commit()  # should not raise


async def test_deleting_agent_cascades_suite_and_cases(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="cascade-agent", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    case = EvalCase(
        suite_id=suite.id, name="c1", input_content="hi", judge_type="exact", expected_output="hi"
    )
    db_session.add(case)
    await db_session.commit()
    # Captured before the delete: SQLite's ON DELETE CASCADE happens purely
    # in the DB, invisible to SQLAlchemy's identity map -- accessing an
    # attribute on the now-expired `suite`/`case` instances after the
    # cascade would try to lazily reload them outside the async greenlet
    # context and raise MissingGreenlet, so the plain id strings are
    # grabbed up front instead.
    suite_id, case_id = suite.id, case.id

    await db_session.delete(agent)
    await db_session.commit()

    # expire_on_commit=False (db/session.py) means the above commit didn't
    # already do this: without it, .get() would return the identity map's
    # stale pre-cascade instance instead of re-querying the DB.
    db_session.expire_all()
    assert await db_session.get(EvalSuite, suite_id) is None
    assert await db_session.get(EvalCase, case_id) is None


async def test_deleting_suite_cascades_runs_and_results(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="cascade-run", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    case = EvalCase(
        suite_id=suite.id, name="c1", input_content="hi", judge_type="exact", expected_output="hi"
    )
    db_session.add(case)
    await db_session.flush()
    run = EvalRun(suite_id=suite.id, case_count=1)
    db_session.add(run)
    await db_session.flush()
    result = EvalCaseResult(run_id=run.id, case_id=case.id, status="passed")
    db_session.add(result)
    await db_session.commit()
    run_id, result_id = run.id, result.id

    await db_session.delete(suite)
    await db_session.commit()

    db_session.expire_all()
    assert await db_session.get(EvalRun, run_id) is None
    assert await db_session.get(EvalCaseResult, result_id) is None


async def test_deleting_case_cascades_results_but_not_run(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="cascade-case", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    case = EvalCase(
        suite_id=suite.id, name="c1", input_content="hi", judge_type="exact", expected_output="hi"
    )
    db_session.add(case)
    await db_session.flush()
    run = EvalRun(suite_id=suite.id, case_count=1)
    db_session.add(run)
    await db_session.flush()
    result = EvalCaseResult(run_id=run.id, case_id=case.id, status="passed")
    db_session.add(result)
    await db_session.commit()
    run_id, result_id = run.id, result.id

    await db_session.delete(case)
    await db_session.commit()

    db_session.expire_all()
    assert await db_session.get(EvalCaseResult, result_id) is None
    assert await db_session.get(EvalRun, run_id) is not None


async def test_suite_cases_relationship_orders_none_implied(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    suite = EvalSuite(name="rel-check", agent_id=agent.id)
    db_session.add(suite)
    await db_session.flush()
    db_session.add(
        EvalCase(
            suite_id=suite.id,
            name="c1",
            input_content="hi",
            judge_type="exact",
            expected_output="hi",
        )
    )
    await db_session.commit()

    reloaded = (await db_session.scalars(select(EvalSuite).where(EvalSuite.id == suite.id))).one()
    await db_session.refresh(reloaded, attribute_names=["cases"])
    assert len(reloaded.cases) == 1
