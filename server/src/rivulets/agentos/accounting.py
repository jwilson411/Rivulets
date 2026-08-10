"""Shared AgentRun accounting (FR-3.5, #28's usage dashboard, #96's run
tracing) -- the one place a `RunOutput` becomes a persisted `AgentRun` row,
so every caller of `agentos.run_agent` records it the same way regardless of
which higher-level path invoked the agent.

Lives here rather than in dispatch/service.py (its original home before
#96) so workflows/nodes.py's `execute_agent_node` can call it too without a
circular import: that module already can't import dispatch/service.py
(its own docstring explains why), but both it and dispatch/service.py
already depend on this `agentos` package.

Also the one place `log_tool_calls` (#100) is invoked, for the same
reason -- every `run_agent` caller's tool calls get audited uniformly
without each caller remembering to do it separately.
"""

from agno.run.agent import RunOutput
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import ModelTier
from rivulets.agentos.pricing import estimate_cost_usd
from rivulets.agentos.tool_audit import log_tool_calls
from rivulets.db.models import Agent, AgentRun


async def record_agent_run(
    db: AsyncSession,
    agent: Agent,
    model: str,
    tier: ModelTier | None,
    status: str,
    run_output: RunOutput,
    *,
    requested_model: str | None = None,
) -> AgentRun:
    """Persist one row of run/token/cost accounting. `model` may be the
    AUTO_MODEL sentinel itself (auto mode whose tier resolution failed,
    run_agent fell through to the already-registered cheap-tier model --
    agentos/service.py's `_build_agno_agent`) rather than a real
    'provider:model_name' string; cost just can't be estimated for that
    row, same as any other unpriced model.

    `requested_model` (#103) is set only when a fallback chain served this
    run instead of the model that was actually asked for -- `model` is
    then the one that answered, `requested_model` the one that failed
    first. Returns the created (flushed, so `.id` is populated) row --
    #96's tracing needs it to size an agent_run span's cost/model."""
    # getattr, not run_output.metrics: dispatch tests monkeypatch run_agent
    # with a plain SimpleNamespace duck-typing only .status/.tools/
    # .get_content_as_string() (test_rivulet_dispatch.py), which has no
    # .metrics attribute at all.
    metrics = getattr(run_output, "metrics", None)
    input_tokens = getattr(metrics, "input_tokens", 0) or 0
    output_tokens = getattr(metrics, "output_tokens", 0) or 0
    total_tokens = getattr(metrics, "total_tokens", 0) or (input_tokens + output_tokens)

    cost_usd = None
    provider, _, model_name = model.partition(":")
    if model_name:
        cost_usd = estimate_cost_usd(provider, model_name, input_tokens, output_tokens)

    run = AgentRun(
        agent_id=agent.id,
        model=model,
        requested_model=requested_model,
        tier=tier,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )
    db.add(run)
    await db.flush()
    await log_tool_calls(db, run, run_output)
    return run
