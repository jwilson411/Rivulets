"""Wraps agno's AgentOS as a pure Python-level agent registry — no HTTP
routes of its own are mounted.

ADR-001 ("AgentOS is the runtime, not something we build") plus
api-design.md's explicit "App Server communicates with AgentOS ... via
Python SDK calls" alternative to the HTTP path: `run_agent()` below calls
`Agent.arun()` in-process, on plain `agno.agent.Agent` objects we
construct and manage ourselves. Since the UI only ever talks to our own
`/api/v1/*` surface (never to AgentOS directly, per api-design.md), that's
also the only HTTP contract this process needs to serve.

Tried mounting AgentOS's own HTTP API onto our app via `AgentOS(base_app=
app).get_app()` first — for the installed agno version (2.8.6), that call
silently dropped every route `app.include_router(api_router)` had already
registered (verified: `/api/v1/*` vanished from `/openapi.json` afterward,
while AgentOS's own `/agents`, `/sessions`, etc. routes were present and
correct). Not chasing that further since it isn't needed for direct
`arun()` calls: if AgentOS's own HTTP surface (tracing UI, Control Plane
compatibility) becomes wanted later, sub-mounting its standalone app via
`app.mount("/agentos", ...)` — built without `base_app=` — is the safer
starting point, since Starlette mounts don't merge route tables the way
`base_app=` apparently does.

Because nothing here goes through AgentOS's own HTTP/route-provisioning
machinery, this module also doesn't use `AgentOS.resync()` — that method's
per-agent setup (defaulting `agent.db`, `agent.initialize_agent()`, etc.)
is bookkeeping for routes we don't mount. Instead each `Agent` we build
gets its `db` set explicitly, which is the same thing bare `Agent(...).
run(...)` usage (the SDK's primary documented pattern, independent of
AgentOS) relies on. `AgentOS.agents` is still kept in sync as a plain
registry, since "what agents does Rivulets know about" is meaningful
bookkeeping on its own (FR-3.2's intent), just without the HTTP layer.

AgentOS gets its own SQLite file (`agentos.db`, not `rivulets.db`) since
it owns its own schema and migrations internally; sharing our file would
mean fighting its migration tooling for no benefit.
"""

import json
import logging
from collections.abc import Callable

from agno.agent import Agent as AgnoAgent
from agno.db.sqlite import SqliteDb
from agno.models.base import Model
from agno.os import AgentOS
from agno.run.agent import (
    RunCompletedEvent,
    RunContentEvent,
    RunErrorEvent,
    RunOutput,
    ToolCallCompletedEvent,
    ToolCallErrorEvent,
    ToolCallStartedEvent,
)
from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.models import (
    AUTO_MODEL,
    UnknownProviderError,
    resolve_model,
    resolve_tier_model,
)
from rivulets.agentos.tool_resolution import resolve_agent_tools
from rivulets.config import get_settings
from rivulets.db.models import Agent
from rivulets.tools.builtin import handoff

logger = logging.getLogger(__name__)

_agent_os: AgentOS | None = None
_agentos_db_instance: SqliteDb | None = None


def _agentos_db() -> SqliteDb:
    global _agentos_db_instance
    if _agentos_db_instance is None:
        settings = get_settings()
        settings.ensure_workspace_dirs()
        _agentos_db_instance = SqliteDb(db_file=str(settings.workspace_dir / "agentos.db"))
    return _agentos_db_instance


def init_agentos() -> AgentOS:
    """Construct the AgentOS singleton. Call once from the app's lifespan
    startup."""
    global _agent_os
    if _agent_os is not None:
        return _agent_os
    _agent_os = AgentOS(id="rivulets", name="Rivulets", db=_agentos_db(), agents=[])
    return _agent_os


def get_agentos() -> AgentOS:
    if _agent_os is None:
        raise RuntimeError("AgentOS not initialized — call init_agentos() at startup")
    return _agent_os


def reset_agentos_for_testing() -> None:
    """Test-only hook, mirroring db.session.override_engine — lets each
    test start from a clean AgentOS singleton instead of leaking agents
    registered by a previous test."""
    global _agent_os, _agentos_db_instance
    _agent_os = None
    _agentos_db_instance = None


async def _build_agno_agent(db: AsyncSession, agent_row: Agent) -> AgnoAgent:
    if agent_row.model == AUTO_MODEL:
        # An "auto" agent's real per-message model is resolved fresh by
        # dispatch/service.py (classify -> resolve_tier_model -> run_agent's
        # model_override) — this registration-time build only needs a
        # runnable placeholder so the agent is always invokable, and
        # "classification failed, fell through" (agentos/models.py) and
        # "never classified, this is the registered model" share the same
        # cheap-tier safety net.
        cheap_provider_model = await resolve_tier_model(db, "cheap")
        if cheap_provider_model is None:
            raise UnknownProviderError("No provider configured for Auto mode.")
        model = await resolve_model(db, cheap_provider_model)
    else:
        model = await resolve_model(db, agent_row.model)
    assigned_tools = await resolve_agent_tools(db, agent_row)
    return AgnoAgent(
        id=agent_row.id,
        name=agent_row.name,
        description=agent_row.description,
        instructions=agent_row.instructions,
        model=model,
        db=_agentos_db(),
        # FR-6.1: unlike the opt-in tools resolved above, handoff is
        # available to every agent unconditionally.
        tools=[handoff, *assigned_tools],
        # #107: a raw JSON Schema dict, agno's own supported shape for
        # output_schema alongside a Pydantic model class -- no dynamic
        # BaseModel construction needed. None means free-form text, same
        # as every agent before this field existed.
        output_schema=json.loads(agent_row.output_schema) if agent_row.output_schema else None,
    )


async def sync_agents(db: AsyncSession) -> None:
    """Rebuild AgentOS's agent registry from our DB (FR-3.2, FR-3.4). Call
    after any agent create/update/delete commit.

    An agent whose provider is missing/unreachable is skipped rather than
    failing the whole sync (NFR-2.4: other agents must keep functioning).
    It simply won't be invokable until its provider is fixed — there's no
    "unavailable" UI indicator for it yet (that's a UI-layer TODO, not
    something this function can surface on its own).
    """
    agent_os = get_agentos()
    result = await db.execute(select(Agent))
    rows = result.scalars().all()

    agno_agents: list[AgnoAgent] = []
    for row in rows:
        try:
            agno_agents.append(await _build_agno_agent(db, row))
        except Exception:
            logger.warning(
                "Skipping agent %r — model could not be resolved", row.name, exc_info=True
            )

    # AgentOS.agents is typed as a union including RemoteAgent/AgentProtocol/
    # AgentFactory, which we never use — list invariance makes pyright treat
    # assigning our narrower list[AgnoAgent] as an error even though it's a
    # safe subset at runtime.
    agent_os.agents = agno_agents  # pyright: ignore[reportAttributeAccessIssue]


async def _run_structured(
    agno_agent: AgnoAgent, message: str, session_id: str, user_id: str
) -> RunOutput:
    """#107: the non-streamed counterpart of run_agent's main loop, taken
    only when `agno_agent.output_schema` is set.

    Streaming and structured output are in tension -- a schema-constrained
    reply generally isn't valid JSON until the model has finished, so
    there's nothing meaningful to hand `on_token` piecemeal. Rather than
    half-stream it, this skips streaming (and therefore on_token/on_status)
    entirely, mirroring dispatch/complexity_classifier.py's own
    `arun(output_schema=..., stream=False)` call -- a data-extraction
    agent doesn't need to feel like it's typing.

    agno parses the reply against `output_schema` internally and sets
    `content_type='dict'` on success; on a malformed/non-compliant
    response it degrades to a warning log and leaves `content` as the raw
    string with `content_type` still `'str'`, *without* flagging the run
    as failed. Promoting that here to RunStatus.error treats "the model
    didn't comply with the schema" as a genuine agent failure like any
    other bad response, per #107's open question -- callers (dispatch/
    service.py's fallback chain, workflow node retries) already handle
    RunStatus.error uniformly, so no new failure-handling path is needed
    for it.
    """
    run_output = await agno_agent.arun(  # pyright: ignore[reportUnknownMemberType]
        message, stream=False, session_id=session_id, user_id=user_id
    )
    if run_output.status == RunStatus.completed and run_output.content_type != "dict":
        run_output.status = RunStatus.error
    return run_output


async def run_agent(
    db: AsyncSession,
    agent_id: str,
    message: str,
    session_id: str,
    user_id: str = "human",
    on_token: Callable[[str], None] | None = None,
    on_status: Callable[[str, str | None], None] | None = None,
    model_override: Model | None = None,
) -> RunOutput:
    """Invoke an agent by our DB id and return its final RunOutput.

    Always streams internally (agno's `arun(stream=True)`) so `on_token`
    can be called with each content delta as it arrives — this is what
    backs the SSE endpoint's `agent_token` events (FR-12.3,
    dispatch/service.py). Callers that don't need incremental output
    (most of dispatch/service.py's own logic — it only checks `.status`
    and calls `.get_content_as_string()`) can omit `on_token` and use the
    returned RunOutput exactly as before streaming existed; this function
    synthesizes one from the terminal stream event since agno's streaming
    mode doesn't hand back a RunOutput object directly.

    `on_status(status, detail)` surfaces agno's tool-call lifecycle events
    (#30, R-9's "agent status indicators") — called with `("executing_tool",
    tool_name)` when a tool call starts and `("thinking", None)` when it
    finishes, so a caller can show "what kind of thing" an agent is doing
    before its first token streams. Deliberately forwards only `tool_name`,
    never `tool.tool_args` — leaking call arguments would violate FR-5.5's
    internal-reasoning suppression. The handoff tool (tools/builtin/
    handoff.py) is available to every agent, so a call to it is reported as
    `("waiting_for_handoff", None)` instead of the generic tool label.

    `model_override` (Auto mode, #23) swaps the model for this call only,
    via a per-call `deep_copy` of the already-registered agent rather than
    mutating the shared registered instance in place — mutating in place
    would race under concurrent invocations of the same auto-mode agent.
    Agents not in auto mode never pass this, so their code path (the
    shared registered instance, unmodified) is exactly what it was before
    this parameter existed.

    A schema-constrained agent (#107, `agent_row.output_schema`) skips all
    of the above and takes the non-streamed `_run_structured` path below
    instead — see that function's docstring for why.
    """
    agent_os = get_agentos()
    agno_agent = next(
        (a for a in (agent_os.agents or []) if isinstance(a, AgnoAgent) and a.id == agent_id),
        None,
    )
    if agno_agent is None:
        raise ValueError(
            f"Agent {agent_id!r} is not registered with AgentOS — call sync_agents() first"
        )
    if model_override is not None:
        agno_agent = agno_agent.deep_copy(update={"model": model_override})

    if agno_agent.output_schema is not None:
        return await _run_structured(agno_agent, message, session_id, user_id)

    final: RunOutput | None = None
    accumulated_content = ""
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # reportUnknownMemberType is about that overload set, not this call.
    async for event in agno_agent.arun(  # pyright: ignore[reportUnknownMemberType]
        message, stream=True, session_id=session_id, user_id=user_id
    ):
        if isinstance(event, RunContentEvent):
            if isinstance(event.content, str):
                accumulated_content += event.content
            if on_token is not None and isinstance(event.content, str):
                on_token(event.content)
        elif isinstance(event, ToolCallStartedEvent):
            if on_status is not None:
                tool_name = event.tool.tool_name if event.tool else None
                if tool_name == "handoff":
                    on_status("waiting_for_handoff", None)
                else:
                    on_status("executing_tool", tool_name)
        elif isinstance(event, (ToolCallCompletedEvent, ToolCallErrorEvent)):
            # Tool call finished either way — back to "thinking" until the
            # next token, tool call, or the run itself completes.
            if on_status is not None:
                on_status("thinking", None)
        elif isinstance(event, RunErrorEvent):
            final = RunOutput(content=event.content, status=RunStatus.error)
        elif isinstance(event, RunCompletedEvent):
            # `tools` carries any tool calls made during the run (e.g. a
            # handoff() call — dispatch/service.py inspects this to detect
            # and act on it after the run completes). `metrics` (tokens,
            # #28's usage dashboard) only arrives on this terminal event —
            # RunContentEvent deltas don't carry it.
            final = RunOutput(
                content=event.content,
                status=RunStatus.completed,
                tools=event.tools,
                metrics=event.metrics,
            )

    if final is None:
        if accumulated_content:
            # Observed in practice with a local OpenAI-compatible backend
            # (ollama): the stream ends after its last RunContentEvent
            # without ever emitting RunCompletedEvent, even though the
            # model finished normally and produced real content. Treating
            # that as an outright failure (the prior behavior) silently
            # discarded a perfectly good reply — synthesize a completed
            # RunOutput from what streamed instead. `tools` is empty here
            # since a real RunCompletedEvent is the only source for it;
            # a handoff call from a provider that hits this path simply
            # won't be detected, same as it wouldn't have been before this
            # RunOutput existed at all.
            final = RunOutput(content=accumulated_content, status=RunStatus.completed)
        else:
            # Nothing streamed at all — a genuine failure, not just a
            # provider that skips the terminal event. NFR-2.4's
            # graceful-degradation contract in dispatch/service.py already
            # treats this as a plain failure via its except-and-skip
            # around this call.
            raise RuntimeError(
                f"Agent {agent_id!r}'s run ended without a completion or error event"
            )
    return final
