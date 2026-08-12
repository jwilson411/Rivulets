"""MCP server management tools (#191): register/reconnect/delete/list
built-in tools mirroring a subset of api/mcp_servers.py's HTTP surface, so
an agent holding the "mcp_servers:manage" scope (#188, agentos/
tool_scopes.py) can register a new MCP server, retry a dropped one, or
remove one on a user's behalf -- another slice of #125's "act on my
behalf" tracking issue, alongside #189's channel tools and #190's agent/
team tools.

Same deliberately side-effect-free shape as channels.py/agents_teams.py:
none of these functions touch the DB or the network directly -- see
agentos/tool_resolution.py's _BUILTIN_REGISTRY docstring for why a shared
`@tool` callable can't have a request-scoped session. The real work
happens in dispatch/service.py's _handle_register_mcp_server_trigger /
_handle_reconnect_mcp_server_trigger / _handle_delete_mcp_server_trigger /
_handle_list_mcp_servers_trigger, which inspect the completed run's tool
calls the same way it already does for create_channel/create_agent.

Deliberately narrower than api/mcp_servers.py's full surface -- two
things it exposes are left out here, on purpose:

- No way to set auth headers or a stdio server's env vars (PUT .../headers,
  PUT .../env). Both hold secret *values* (an API key, a bearer token),
  and the only way a value could reach either endpoint through a built-in
  tool call is as a literal tool argument the model itself produces --
  which means the raw secret would sit in this rivulet's conversation
  history and get sent to the model provider as part of the prompt, the
  exact "plaintext tool argument" #191's issue body says to avoid.
  security/credentials.py has no "reference an already-stored secret by
  name" primitive a tool could accept instead, so there's no safe
  parameter shape to give this function today -- registering/changing a
  server's auth stays a human-in-the-UI action (still owner-gated at the
  route, unchanged) until that primitive exists.
- No stdio transport (`transport="stdio"`, #187) in register_mcp_server.
  A stdio server is a local subprocess Rivulets itself execs on the
  model's say-so -- comparable in reach to execute_python (tools/builtin/
  code_exec.py, #100's SENSITIVE_BUILTIN_TOOL_NAMES), and the HTTP route
  reflects that by requiring a live owner session for *every* stdio
  registration, not just a one-time scope grant (api/mcp_servers.py's
  module docstring). #188's scope model only expresses a standing grant,
  not a live-session check, so it can't reproduce that stronger gate --
  streamable-http registration only requires owner-gating when headers
  are involved (already excluded above), so it's the one register_mcp_server
  can safely offer.

reconnect_mcp_server and delete_mcp_server aren't subject to either
concern -- reconnecting reuses whatever headers/env are already stored
(no new secret enters the picture) and deleting never touches a secret's
*value*, so both are offered for either transport, matching how neither
endpoint carries extra gating beyond CurrentWorkspaceId in api/
mcp_servers.py today. list_mcp_servers is read-only and deliberately left
unscoped, mirroring list_channels/list_agents/list_teams.
"""

from agno.tools import tool


@tool
def register_mcp_server(name: str, url: str) -> str:
    """Register a new streamable-HTTP MCP server by its endpoint URL, then
    connect to it and discover its tools. Only unauthenticated
    streamable-HTTP servers are supported here -- a server that needs
    auth headers or runs as a local (stdio) process has to be added
    through the UI instead, since entering credentials or enabling local
    process execution isn't something this tool accepts. Registration
    still succeeds even if the server can't be reached right now; use
    reconnect_mcp_server to retry later."""
    return f"Requested registration of MCP server {name!r} at {url!r}."


@tool
def reconnect_mcp_server(server: str) -> str:
    """Retry connecting to a previously registered MCP server and
    re-discover its tools. `server` is the server's id or its current
    name. Use this after a server that failed to connect (or was
    temporarily down) becomes reachable again."""
    return f"Requested reconnect of MCP server {server!r}."


@tool
def delete_mcp_server(server: str) -> str:
    """Permanently remove a registered MCP server and every tool it
    contributed. `server` is the server's id or its current name. This
    cannot be undone -- any agent with one of this server's tools
    assigned loses access to it immediately."""
    return f"Requested removal of MCP server {server!r}."


@tool
def list_mcp_servers() -> str:
    """List the workspace's registered MCP servers, including which are
    currently connected, so you can find a server's id/name before acting
    on it with the other MCP server tools."""
    return "Looking up the workspace's MCP servers."
