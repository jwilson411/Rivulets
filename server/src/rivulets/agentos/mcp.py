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
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from agno.tools.mcp import MCPTools
from agno.tools.mcp.params import StreamableHTTPClientParams

from rivulets.db.models import MCPServer
from rivulets.security.credentials import CredentialStoreError, get_secret

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
    # The MCP tool's raw inputSchema (JSON Schema) — can express conditional/
    # dependent structure via if/then/else, dependentRequired,
    # dependentSchemas, oneOf, etc. Empty dict if the server advertised none.
    input_schema: dict[str, Any] = field(default_factory=lambda: {})


def mcp_header_ref(server_id: str) -> str:
    return f"mcp-server-headers:{server_id}"


def get_server_headers(server: MCPServer) -> dict[str, str] | None:
    """The custom HTTP headers (#124) configured for `server`, or None if
    it has none. Values live in the keychain (security/credentials.py),
    keyed by mcp_header_ref -- `header_names_json` on the row is only ever
    the header *names*, so a lookup is needed even to know there's
    anything to fetch."""
    if not server.header_names_json:
        return None
    ref = mcp_header_ref(server.id)
    try:
        raw = get_secret(ref)
    except CredentialStoreError:
        logger.warning(
            "MCP server %r has header_names_json but no stored secret at ref %r "
            "-- treating as no headers",
            server.name,
            ref,
        )
        return None
    return json.loads(raw)


async def discover_tools(
    url: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
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
        _run_handshake(url, timeout_seconds, headers)
    )
    try:
        return await asyncio.wait_for(
            task, timeout=timeout_seconds + _TASK_ISOLATION_BUFFER_SECONDS
        )
    except MCPConnectionError:
        raise
    except (TimeoutError, asyncio.CancelledError) as exc:
        raise MCPConnectionError(f"Could not connect to MCP server at {url}: {exc}") from exc


async def _run_handshake(
    url: str, timeout_seconds: int, headers: dict[str, str] | None = None
) -> list[DiscoveredTool]:
    # Only build server_params when there are headers to carry -- passing
    # StreamableHTTPClientParams unconditionally would substitute its own
    # 30s default `timeout` for the connect-phase read timeout MCPTools
    # would otherwise derive from `timeout_seconds` (see agno's
    # MCPTools._connect: `params_timeout = streamable_http_params.get(
    # "timeout", self.timeout_seconds)`), silently changing behavior for
    # every server that has no headers configured.
    server_params = (
        StreamableHTTPClientParams(
            url=url, headers=headers, timeout=timedelta(seconds=timeout_seconds)
        )
        if headers
        else None
    )
    mcp_tools = MCPTools(
        url=url,
        transport="streamable-http",
        timeout_seconds=timeout_seconds,
        server_params=server_params,
    )
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
        DiscoveredTool(
            name=fn.name,
            description=fn.description or "",
            # fn.parameters is agno's Function.parameters, set to the MCP
            # tool's own tool.inputSchema verbatim by MCPTools.build_tools()
            # — not reshaped, so conditional/dependent keywords survive.
            input_schema=fn.parameters or {},
        )
        for fn in functions.values()
    ]
