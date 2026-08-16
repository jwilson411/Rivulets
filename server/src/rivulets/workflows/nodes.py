"""Built-in workflow node executors (#24).

`node_type == 'agent'` reuses `agentos.run_agent` directly — an agent
node is not a distinct execution path, just this engine's caller of the
same entry point dispatch/service.py uses. The remaining types are plain
functions, not agno `Function`/LLM tools: they're invoked by the workflow
engine itself (workflows/engine.py), never by an agent's own tool-calling
loop, so there's no need to route them through the LLM-facing tool
machinery in tools/builtin/ (see tool_resolution.py's docstring for that
distinction).

Each executor takes the previous node's output as plain text and returns
plain text — "a node's output becomes the next node's input, unmodified"
(issue #24) applies uniformly whether that next node is an agent or
another utility node.

'human_input' (#83) has no executor here — unlike every other type, it
never runs synchronously to completion. workflows/engine.py's `_advance`
intercepts it before it would ever reach `_execute_node`, pausing that
branch (WorkflowRun.status='awaiting_human') until a human replies in the
rivulet; the reply becomes the paused node's output the same way any
other node's return value would, via `resume_workflow`.

'workflow' (#85) has no executor here either, for a different reason:
invoking a nested workflow means calling `run_workflow` itself, which is
defined in workflows/engine.py -- a module-level import of it here would
be circular (engine.py already imports this module for every other
executor). `_execute_workflow_node` lives in engine.py instead and is
wired into `_execute_node`'s dispatch there directly, alongside (not
through) this module's executors.
"""

import json

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from agno.run.agent import RunOutput
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos import run_agent
from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.models import resolve_model
from rivulets.agentos.tool_audit import ensure_unattended_tools_allowed
from rivulets.db.models import Agent, Rivulet, WorkflowNode
from rivulets.sync.publish import publish_current_state
from rivulets.tracing import TraceContext, finish_span, start_span

NODE_TYPES = (
    "agent",
    "summarize",
    "transform",
    "conditional",
    "merge",
    "human_input",
    "workflow",
)

_SUMMARIZE_INSTRUCTIONS = (
    "Summarize the given text concisely, preserving the key points. "
    "Respond with the summary only, no preamble."
)


class ConditionNotMetError(Exception):
    """Raised by the 'conditional' node when its predicate doesn't match
    the input — signals the engine to end the run early (not a failure;
    see workflows/engine.py's handling)."""


class NoProviderConfiguredError(Exception):
    """Raised by 'summarize' when no LLM provider is available to call."""


def _load_config(node: WorkflowNode) -> dict[str, object]:
    return json.loads(node.config_json) if node.config_json else {}


async def execute_agent_node(
    db: AsyncSession,
    node: WorkflowNode,
    session_id: str,
    input_content: str,
    trace_ctx: TraceContext | None = None,
    unattended: bool = False,
    rivulet_id: str | None = None,
    ancestry: frozenset[str] = frozenset(),
) -> str:
    """#96: also records an AgentRun row (agentos/accounting.py's
    record_agent_run, the same helper dispatch/service.py uses) and an
    agent_run trace span -- before this, a workflow's 'agent' node ran
    through `run_agent` same as any other agent invocation but never got
    accounted for: invisible to the usage dashboard (#28) and to a trace
    built from AgentRun data. No tier/fallback-chain support here (unlike
    dispatch/service.py's `_invoke_agent`) -- workflow agent nodes have
    never done auto-tier classification or fallback-model retries, and
    this doesn't add either, just accounting for what already runs.

    `unattended` (#100, from WorkflowRun.unattended via the engine's
    `_RunContext`) gates this node before it ever calls `run_agent` at all
    when `agent` has an unapproved sensitive tool assigned -- see
    tool_audit.py's `ensure_unattended_tools_allowed`. Checked inside the
    span's try/except like any other failure this function can raise, so
    a blocked node shows up in the trace/run history the same way a
    genuine agent error would, not as something invisible.

    #320: also runs dispatch/budgets.py's check before ever calling
    `run_agent` -- a workflow agent node is a spend path same as a
    channel-dispatched one, and #246 only wired the check into channel
    dispatch. No team_id to pass here (unlike dispatch/service.py's
    channel.team_id -- a workflow isn't scoped to a team), but team-scope
    caps on any team the agent belongs to still apply: _applicable_caps
    resolves them from the agent's own TeamAgent memberships (#354).
    A BudgetCapBlockedError
    propagates like any other node failure (see this function's
    docstring above and _run_node_with_retries' uniform handling) --
    there's no rivulet/channel context here to post a system_alert
    Message into the way channel dispatch does, so the block surfaces as
    the node/run's own error_message instead.

    #360: after the run completes, its tool calls are fed through the
    same builtin side-effect executor channel dispatch uses
    (dispatch/service.py's apply_builtin_tool_triggers) — before this, an
    agent node could call run_workflow/create_channel/etc. and the stub
    tool's "Requested..." reply was the *only* thing that happened; the
    side effects only ever ran on the channel-dispatch path. `rivulet_id`
    (the run's own rivulet, from the engine) is what the trigger handlers
    post their confirmation/rejection messages into; `ancestry` (the
    engine's _RunContext.ancestry) makes an agent-triggered run_workflow
    a properly-guarded nested run instead of an unguarded recursion
    vector. Both default to "skip trigger processing" so direct callers
    (evals) keep the old run-only behavior."""
    if node.agent_id is None:
        raise ValueError(f"Node {node.name!r} has no agent assigned")
    agent = await db.get(Agent, node.agent_id)
    if agent is None:
        raise ValueError(f"Node {node.name!r} references a deleted agent")

    # Lazy import: dispatch/__init__.py -> dispatch.service -> api.agents
    # -> api (package init) -> api.workflows -> this package -> this
    # module, so a module-level import of dispatch.budgets here would be
    # circular -- same reasoning as execute_summarize_node's lazy import
    # of dispatch.rule_generation below.
    from rivulets.dispatch.budgets import enforce_budget_caps

    await enforce_budget_caps(db, agent, None)

    span_id = await start_span(
        db, trace_ctx, span_type="agent_run", entity_id=None, name=agent.name
    )
    try:
        if unattended:
            await ensure_unattended_tools_allowed(db, agent)
        run_output = await run_agent(
            db, agent.id, input_content, session_id=session_id, user_id="workflow"
        )
    except Exception:
        # _run_node_with_retries retries/reports this uniformly with every
        # other node failure -- just close out the span first so it
        # doesn't sit 'running' forever.
        await finish_span(db, span_id, status="error")
        raise
    run = await record_agent_run(db, agent, agent.model, None, "completed", run_output)
    await finish_span(
        db,
        span_id,
        status="completed",
        entity_id=run.id,
        model=agent.model,
        cost_usd=run.cost_usd,
        total_tokens=run.total_tokens,
    )

    # #360 (see docstring above). getattr, not run_output.tools: same
    # tolerance for the test suite's SimpleNamespace run_agent doubles as
    # tool_audit.py's log_tool_calls — and a run that made no tool calls
    # (the common case) skips the rivulet fetch and dispatch import
    # entirely.
    if rivulet_id is not None and getattr(run_output, "tools", None):
        # Lazy import for the same circular-import reason as
        # dispatch.budgets above.
        from rivulets.dispatch.service import apply_builtin_tool_triggers

        rivulet = await db.get(Rivulet, rivulet_id)
        assert rivulet is not None
        messages = await apply_builtin_tool_triggers(
            db,
            rivulet,
            agent,
            run_output,
            # Nest anything a trigger starts (e.g. a child workflow_run
            # span) under this node's agent_run span, the same way
            # _invoke_agent's child_trace_ctx nests triggers under the
            # dispatching agent's own span.
            trace_ctx=TraceContext(trace_ctx.trace_id, span_id) if trace_ctx is not None else None,
            workflow_ancestry=ancestry,
            unattended=unattended,
        )
        # The handlers staged their confirmation/rejection Messages on
        # `db`; channel dispatch returns those up to api/rivulets.py to
        # commit/publish, but this engine has no such outer handler —
        # same self-contained commit/publish as engine.py's _post_message.
        await db.commit()
        for message in messages:
            await db.refresh(message)
            await publish_current_state(db, "message", message.id)

    return run_output.get_content_as_string() or ""  # pyright: ignore[reportUnknownMemberType]


def execute_transform_node(node: WorkflowNode, input_content: str) -> str:
    """config: {"template": "..."} — "{input}" is replaced verbatim (a
    plain string substitution, not str.format, so template text containing
    other brace characters can't raise or be misinterpreted). An absent or
    empty template passes the input through unchanged."""
    config = _load_config(node)
    template = config.get("template")
    if not isinstance(template, str) or not template:
        return input_content
    return template.replace("{input}", input_content)


async def _run_summarizer(model: Model, input_content: str) -> RunOutput:
    """Isolated seam for tests to monkeypatch — same idiom as
    rule_generation.py's `_run_generator` / llm_fallback.py's
    `_run_decision`. Returns the raw RunOutput (not just its content) so
    the caller can record its token/cost accounting (#354)."""
    summarizer = AgnoAgent(model=model, instructions=_SUMMARIZE_INSTRUCTIONS)
    return await summarizer.arun(  # pyright: ignore[reportUnknownMemberType]
        input_content, stream=False
    )


async def execute_summarize_node(db: AsyncSession, node: WorkflowNode, input_content: str) -> str:
    """No dedicated Agent DB row needed — reuses the same "pick a cheap
    dispatcher-tier model" policy as dispatch/llm_fallback.py and
    rule_generation.py for this kind of ad-hoc, non-agent LLM call.
    Imported lazily: dispatch/service.py also reaches into this package
    (dispatch/service.py's _handle_run_workflow_trigger, #24) to run the
    run_workflow tool, so a module-level import of a dispatch submodule
    here would risk a circular import depending on which package happens
    to load first at app startup.

    #354 (leftover of #320): this call burns a provider key same as any
    agent run, but never checked budget caps or recorded its spend --
    a tripped workspace hard_stop didn't stop it, and its cost never
    counted toward any cap's window. Now enforces caps first (workspace
    scope only: there's no Agent row and no team to attribute to, same
    shape as knowledge_base ingest's workspace-only check) and records
    the run via record_agent_run with agent_id=None,
    source='summarize_node' -- the same "unattributable billed call"
    precedent as #246's dispatcher_call and #320's embedding rows. A
    BudgetCapBlockedError propagates like any other node failure (same
    handling as execute_agent_node's -- the block becomes the node/run's
    own error_message)."""
    from rivulets.dispatch.budgets import enforce_budget_caps
    from rivulets.dispatch.rule_generation import pick_dispatcher_model

    await enforce_budget_caps(db, None, None)

    provider_model = await pick_dispatcher_model(db)
    if provider_model is None:
        raise NoProviderConfiguredError("No provider configured for the summarize node")
    model = await resolve_model(db, provider_model)
    run_output = await _run_summarizer(model, input_content)
    await record_agent_run(
        db, None, provider_model, None, "completed", run_output, source="summarize_node"
    )
    content = run_output.content
    return content if isinstance(content, str) else str(content)


def execute_conditional_node(node: WorkflowNode, input_content: str) -> str:
    """config: {"contains": "keyword"} — case-insensitive substring check
    against the input. Raises ConditionNotMetError to end the run early
    when the predicate fails; an absent/empty predicate always passes
    (a conditional node with no condition configured is a no-op, not a
    silent stop)."""
    config = _load_config(node)
    needle = config.get("contains")
    if not isinstance(needle, str) or not needle:
        return input_content
    if needle.lower() not in input_content.lower():
        raise ConditionNotMetError(f"Input did not contain {needle!r}")
    return input_content


def execute_merge_node(node: WorkflowNode, inputs: list[str]) -> str:
    """Combines the outputs of every sibling branch that joined at this
    merge node (workflows/engine.py's `_resolve_merge_arrivals` — see its
    module docstring for exactly which arrivals count as siblings). `inputs`
    is already in a stable order (the joining edges' own creation order),
    not arrival/completion order, so a merge's output doesn't depend on
    which branch happened to finish first.

    config: {"template": "..."} — like transform's "{input}", but one
    placeholder per contributing branch ("{input0}", "{input1}", ...),
    replaced verbatim (not str.format, same reasoning as transform: template
    text with other brace characters can't raise or be misinterpreted). A
    placeholder with no matching branch (more placeholders than inputs)
    is left as literal text.

    No template configured (the default): a JSON array of the branch
    outputs, in that same order — always unambiguous and directly usable
    by a downstream agent/transform node, regardless of how many branches
    arrived. This applies even to a single arrival (a merge node reached
    by only one live branch, e.g. because a sibling dead-ended elsewhere)
    for predictability: the shape of a merge node's output never depends
    on how many branches happened to survive to it."""
    config = _load_config(node)
    template = config.get("template")
    if isinstance(template, str) and template:
        result = template
        for i, value in enumerate(inputs):
            result = result.replace(f"{{input{i}}}", value)
        return result
    return json.dumps(inputs)
