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
registry, since "what agents does Agent Hive know about" is meaningful
bookkeeping on its own (FR-3.2's intent), just without the HTTP layer.

AgentOS gets its own SQLite file (`agentos.db`, not `agent-hive.db`) since
it owns its own schema and migrations internally; sharing our file would
mean fighting its migration tooling for no benefit.
"""

import logging
from collections.abc import Callable

from agno.agent import Agent as AgnoAgent
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.run.agent import RunCompletedEvent, RunContentEvent, RunErrorEvent, RunOutput
from agno.run.base import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hive.agentos.models import resolve_model
from agent_hive.config import get_settings
from agent_hive.db.models import Agent
from agent_hive.tools.builtin import handoff

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
    _agent_os = AgentOS(id="agent-hive", name="Agent Hive", db=_agentos_db(), agents=[])
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
    model = await resolve_model(db, agent_row.model)
    # TODO(FR-8.2): resolve agent_row's assigned tools (agent_tool join ->
    # tool.tool_type) into agno tool callables. Built-in tools already
    # exist as agno @tool functions (tools/builtin/) — this needs the join
    # query and a tool_type dispatch, not new tool implementations.
    return AgnoAgent(
        id=agent_row.id,
        name=agent_row.name,
        description=agent_row.description,
        instructions=agent_row.instructions,
        model=model,
        db=_agentos_db(),
        # FR-6.1: unlike the opt-in built-ins above, handoff is available
        # to every agent unconditionally.
        tools=[handoff],
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


async def run_agent(
    db: AsyncSession,
    agent_id: str,
    message: str,
    session_id: str,
    user_id: str = "human",
    on_token: Callable[[str], None] | None = None,
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

    final: RunOutput | None = None
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # reportUnknownMemberType is about that overload set, not this call.
    async for event in agno_agent.arun(  # pyright: ignore[reportUnknownMemberType]
        message, stream=True, session_id=session_id, user_id=user_id
    ):
        if isinstance(event, RunContentEvent):
            if on_token is not None and isinstance(event.content, str):
                on_token(event.content)
        elif isinstance(event, RunErrorEvent):
            final = RunOutput(content=event.content, status=RunStatus.error)
        elif isinstance(event, RunCompletedEvent):
            # `tools` carries any tool calls made during the run (e.g. a
            # handoff() call — dispatch/service.py inspects this to detect
            # and act on it after the run completes).
            final = RunOutput(content=event.content, status=RunStatus.completed, tools=event.tools)

    if final is None:
        # Every observed run ends in RunCompletedEvent or RunErrorEvent;
        # this would mean the stream closed without either, which NFR-2.4's
        # graceful-degradation contract in dispatch/service.py already
        # treats as a plain failure via its except-and-skip around this call.
        raise RuntimeError(f"Agent {agent_id!r}'s run ended without a completion or error event")
    return final
