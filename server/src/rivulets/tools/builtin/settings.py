"""Workspace settings management tools (#193): read/update built-in tools
mirroring api/settings.py's GET/PATCH surface, so an agent holding the
"settings:manage" scope (#188, agentos/tool_scopes.py) can inspect or
change workspace-wide policy (dispatcher/guard/rivulet/sync behavior) on
a user's behalf -- the fifth and last slice of #125's "act on my behalf"
tracking issue, alongside #189-#192's channel/agent-team/MCP-server/
workflow tools.

Same deliberately side-effect-free shape as channels.py/agents_teams.py/
mcp_servers.py/workflows.py: neither function touches the DB directly --
see agentos/tool_resolution.py's _BUILTIN_REGISTRY docstring for why a
shared `@tool` callable can't have a request-scoped session. The real
work happens in dispatch/service.py's _handle_get_workspace_settings_trigger/
_handle_update_workspace_settings_trigger, which inspect the completed
run's tool calls the same way it already does for create_channel/
create_workflow/register_mcp_server.

Unlike every prior sub-issue's read-only list tool (list_channels,
list_agents, list_mcp_servers, list_workflows), get_workspace_settings
carries a required_scope too -- api/settings.py's GET route is
OwnerGrant-gated same as its PATCH, so reading settings needs the same
standing grant reading a channel or workflow list never did. See
tool_scopes.py's BUILTIN_TOOL_SCOPES comment for the full reasoning.
"""

from agno.tools import tool


@tool
def get_workspace_settings() -> str:
    """Look up the workspace's current settings -- dispatcher, guard,
    rivulet summarization, and sync behavior, each as a key/value pair
    (e.g. "guard.turn_limit": 10). Includes every setting's current
    value, whether it's been explicitly changed or still holds its
    default."""
    return "Looking up the workspace's settings."


@tool
def update_workspace_settings(settings: dict[str, object]) -> str:
    """Update one or more workspace settings. `settings` maps setting
    keys (e.g. "guard.turn_limit", "rivulet.summarization_enabled") to
    their new values -- only keys already known to the workspace are
    accepted; an unrecognized key fails the whole update, the same as
    the underlying API. Only the keys you include are changed; every
    other setting is left as-is."""
    return f"Requested update to workspace settings: {', '.join(sorted(settings))}."
