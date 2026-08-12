"""Resolves an agent's assigned tools (agent_tool join -> tool.tool_type)
into real agno tool objects (FR-8.2). Until this module existed,
_build_agno_agent only ever attached the hardcoded `handoff` tool — every
builtin/custom/mcp tool built earlier (tools/builtin/, api/tools.py's
custom tool CRUD + sync, api/mcp_servers.py's MCP discovery) was
registered in the DB but never actually reachable by an agent.

Three tool_types, three resolution strategies:
  - builtin: looked up by name in a fixed registry built from
    tools/builtin/'s @tool-decorated functions. These Tool rows don't
    exist in the DB until seed_builtin_tools() runs (called once at app
    startup, idempotent by name) — nothing seeded them before this
    module, so there was no way to even reference a builtin tool via
    agent_tool, separately from resolution not existing.
  - custom: the tool's Python file (Tool.source_path) is dynamically
    loaded fresh on every resolution (not cached — a custom tool's source
    can change between agent builds, e.g. via rollback_tool_version) and
    the function matching the tool's own name is extracted. A tool
    row whose file is missing, empty (the state right after create() —
    there's no "save from editor" endpoint yet, a separate known gap),
    or doesn't define a matching @tool function is skipped with a
    warning, not a hard failure (NFR-2.4: one broken tool shouldn't take
    the whole agent offline).
  - mcp: a fresh, *unconnected* agno.tools.mcp.MCPTools scoped to just
    this one tool (include_tools=[mcp_tool_name], not the server's whole
    catalog) is handed to the Agent. Deliberately unconnected: agno's own
    Agent.arun() (agno/agent/_init.py's connect_mcp_tools/
    disconnect_mcp_tools) detects any MCPTools in agent.tools that isn't
    already connected, connects it before the run, and disconnects it
    after — confirmed by reading agno's source before relying on it,
    since getting this wrong would mean either leaking a live MCP
    connection per agent-build (sync_agents() rebuilds the whole AgentOS
    registry on every agent create/update) or handing agents a dead
    connection. Using agno's own lifecycle management means this module
    doesn't need to track or clean up MCP connections at all.
"""

import importlib.util
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from agno.tools.function import Function
from agno.tools.mcp import MCPTools
from agno.tools.mcp.params import StreamableHTTPClientParams
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.mcp import build_stdio_command, get_server_env, get_server_headers
from rivulets.db.models import Agent, AgentTool, MCPServer, Tool
from rivulets.tools.builtin import (
    cancel_schedule,
    execute_python,
    http_request,
    list_files,
    list_schedules,
    query_workspace_db,
    read_attached_file,
    read_file,
    run_workflow,
    schedule_workflow,
    search_knowledge_base,
    web_search,
    write_file,
)

logger = logging.getLogger(__name__)

_BUILTIN_FUNCTIONS: tuple[Function, ...] = (
    cancel_schedule,
    execute_python,
    http_request,
    list_files,
    list_schedules,
    query_workspace_db,
    read_attached_file,
    read_file,
    run_workflow,
    schedule_workflow,
    search_knowledge_base,
    web_search,
    write_file,
)
_BUILTIN_REGISTRY: dict[str, Function] = {fn.name: fn for fn in _BUILTIN_FUNCTIONS}

# #100: builtin tools with real blast radius (arbitrary code exec,
# arbitrary outbound HTTP, local filesystem writes, DB access) — read_file/
# list_files are deliberately excluded (read-only, lower risk) as are
# run_workflow/schedule_workflow/*_schedule/web_search/handoff/
# search_knowledge_base (#98 -- also read-only). Used both
# to seed Tool.sensitive below and by tool_audit.py's unattended gate,
# which checks an agent's *assigned* tools against this set rather than a
# live Tool.sensitive lookup for the two purpose-built builtin functions
# that most matter (execute_python, http_request) — see tool_audit.py.
SENSITIVE_BUILTIN_TOOL_NAMES = frozenset(
    {"execute_python", "http_request", "write_file", "query_workspace_db"}
)


async def seed_builtin_tools(db: AsyncSession) -> None:
    """Ensures a `tool` row (tool_type='builtin') exists for each function
    in the registry above, so builtin tools can be assigned to agents via
    the same agent_tool join table custom/mcp tools use. Idempotent by
    name — call on every app startup (mirrors sync_agents()'s own
    call-every-startup pattern), not just the first.

    Also patches `sensitive` on every existing builtin row to match
    SENSITIVE_BUILTIN_TOOL_NAMES (#100) — not just set at insert time —
    since an install that seeded these rows before #100 existed would
    otherwise carry `sensitive=False` forever with no other code path
    that ever revisits it."""
    result = await db.execute(select(Tool).where(Tool.tool_type == "builtin"))
    existing = {row.name: row for row in result.scalars().all()}
    for fn in _BUILTIN_FUNCTIONS:
        sensitive = fn.name in SENSITIVE_BUILTIN_TOOL_NAMES
        if fn.name not in existing:
            db.add(
                Tool(
                    name=fn.name,
                    description=fn.description or "",
                    tool_type="builtin",
                    sensitive=sensitive,
                )
            )
        elif existing[fn.name].sensitive != sensitive:
            existing[fn.name].sensitive = sensitive
    await db.commit()


def _load_custom_tool(tool_row: Tool) -> Function | None:
    if not tool_row.source_path:
        return None
    path = Path(tool_row.source_path)
    if not path.exists():
        return None
    # A unique-per-call module name (not the tool's own name) avoids any
    # chance of colliding with a real installed package, and there's no
    # need to register in sys.modules since these are standalone scripts,
    # not part of a package that needs relative imports resolved.
    spec = importlib.util.spec_from_file_location(f"rivulets_custom_tool_{uuid.uuid4().hex}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = getattr(module, tool_row.name, None)
    return candidate if isinstance(candidate, Function) else None


async def resolve_agent_tools(db: AsyncSession, agent_row: Agent) -> list[Any]:
    """Returns the agno tool objects for everything assigned to
    `agent_row` via the agent_tool join table — the caller (agentos/
    service.py's _build_agno_agent) adds `handoff` on top unconditionally
    (FR-6.1), so this only returns the opt-in tools."""
    result = await db.execute(
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent_row.id)
    )
    tool_rows = list(result.scalars().all())

    resolved: list[Any] = []
    for tool_row in tool_rows:
        try:
            if tool_row.tool_type == "builtin":
                fn = _BUILTIN_REGISTRY.get(tool_row.name)
                if fn is None:
                    logger.warning(
                        "Unknown builtin tool %r assigned to agent %r — skipping",
                        tool_row.name,
                        agent_row.name,
                    )
                    continue
                resolved.append(fn)

            elif tool_row.tool_type == "custom":
                custom_fn = _load_custom_tool(tool_row)
                if custom_fn is None:
                    logger.warning(
                        "Could not load custom tool %r for agent %r (missing/empty/invalid "
                        "source file) — skipping",
                        tool_row.name,
                        agent_row.name,
                    )
                    continue
                resolved.append(custom_fn)

            elif tool_row.tool_type == "mcp":
                if not tool_row.mcp_server_id or not tool_row.mcp_tool_name:
                    continue
                server = await db.get(MCPServer, tool_row.mcp_server_id)
                if server is None:
                    logger.warning(
                        "MCP tool %r for agent %r references a missing server — skipping",
                        tool_row.name,
                        agent_row.name,
                    )
                    continue
                if server.transport == "stdio":
                    # Enforced at create time (api/mcp_servers.py's
                    # MCPServerCreate validator): command is required for
                    # stdio transport.
                    assert server.command is not None
                    args = json.loads(server.args_json) if server.args_json else None
                    resolved.append(
                        MCPTools(
                            command=build_stdio_command(server.command, args),
                            env=get_server_env(server),
                            transport="stdio",
                            include_tools=[tool_row.mcp_tool_name],
                        )
                    )
                else:
                    # Enforced at create time: url is required for
                    # streamable-http transport.
                    assert server.url is not None
                    headers = get_server_headers(server)
                    resolved.append(
                        MCPTools(
                            url=server.url,
                            transport="streamable-http",
                            include_tools=[tool_row.mcp_tool_name],
                            server_params=(
                                StreamableHTTPClientParams(url=server.url, headers=headers)
                                if headers
                                else None
                            ),
                        )
                    )
        except Exception:
            logger.warning(
                "Failed to resolve tool %r for agent %r",
                tool_row.name,
                agent_row.name,
                exc_info=True,
            )

    return resolved
