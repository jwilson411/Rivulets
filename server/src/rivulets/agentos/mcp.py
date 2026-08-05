"""MCP server discovery (FR-8.5): connect to an external MCP server and
list its tools. Uses agno's MCPTools wrapper (agno.tools.mcp) rather than
the raw `mcp` SDK directly, since that's the same object type FR-8.2's
(still-unbuilt) tool-assignment resolution would eventually hand to an
agent's `tools=[...]` list — discovery and actual runtime usage share the
same underlying client, just not wired together yet.

Requires `mcp>=1.9.2,<2` specifically — agno 2.8.6 pins that range itself
(`agno[mcp]`'s extra), and the current `mcp` release on PyPI is 2.0.0+,
which renamed `McpError` to `MCPError` and breaks agno's import at
`agno/utils/mcp.py`. Don't bump this without checking agno's own
constraint first.
"""

import asyncio
import logging
from dataclasses import dataclass

from agno.tools.mcp import MCPTools

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10
# How much longer than MCPTools' own internal timeout we wait before giving
# up on it from the outside — see discover_tools()'s docstring for why an
# outer bound exists at all.
_TASK_ISOLATION_BUFFER_SECONDS = 5


class MCPConnectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveredTool:
    name: str
    description: str


async def discover_tools(
    url: str, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
) -> list[DiscoveredTool]:
    """Connect to the MCP server at `url` over streamable-http, list its
    tools, then disconnect. Raises MCPConnectionError on any failure (bad
    URL, connection refused, protocol/handshake error, timeout) — callers
    decide whether that's fatal or something to degrade gracefully from
    (api/mcp_servers.py does the latter, matching NFR-2.4's pattern for
    every other unreachable-external-service case in this app).

    Runs the actual handshake in its own asyncio Task rather than inline.
    MCPTools (and the mcp SDK beneath it) drive nested anyio cancel scopes
    during connect()/initialize(), and anyio tracks "the current cancel
    scope" per asyncio Task. When a connection to a dead host stalls,
    MCPTools' own internal timeout cancels mid-handshake — and in practice
    that cancellation doesn't stay contained: it surfaces here as a raw
    asyncio.CancelledError (a BaseException, so a plain `except Exception`
    never caught it) and, left unhandled, corrupts the *caller's* anyio
    cancel-scope stack on its way out. That showed up as `RuntimeError:
    Attempted to exit a cancel scope that isn't the current task's current
    cancel scope` during FastAPI's own request-scope teardown — a 500 in
    place of what should have been a graceful "can't reach this server".
    Running the handshake in an isolated Task, bounded by our own
    asyncio.wait_for(), confines any such corruption to a task nobody
    awaits again instead of poisoning the request-handling task.
    """
    task: asyncio.Task[list[DiscoveredTool]] = asyncio.ensure_future(
        _run_handshake(url, timeout_seconds)
    )
    try:
        return await asyncio.wait_for(
            task, timeout=timeout_seconds + _TASK_ISOLATION_BUFFER_SECONDS
        )
    except MCPConnectionError:
        raise
    except (TimeoutError, asyncio.CancelledError) as exc:
        raise MCPConnectionError(f"Could not connect to MCP server at {url}: {exc}") from exc


async def _run_handshake(url: str, timeout_seconds: int) -> list[DiscoveredTool]:
    mcp_tools = MCPTools(url=url, transport="streamable-http", timeout_seconds=timeout_seconds)
    try:
        await mcp_tools.connect()
        await mcp_tools.initialize()
        functions = mcp_tools.get_functions()
    except Exception as exc:
        raise MCPConnectionError(f"Could not connect to MCP server at {url}: {exc}") from exc
    finally:
        try:
            await mcp_tools.close()
        except Exception:
            logger.warning("Error closing MCP connection to %s", url, exc_info=True)

    return [
        DiscoveredTool(name=fn.name, description=fn.description or "") for fn in functions.values()
    ]
