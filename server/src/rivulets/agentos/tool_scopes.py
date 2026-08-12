"""Capability-scope catalog for built-in tools with reach beyond an
agent's own conversation -- channel/agent-team/MCP-server/workflow/
settings management (#125's tracking issue, broken into per-category
sub-issues #189-#193). This module is the bounding mechanism #188 asked
for, landed ahead of the tools that use it so each sub-issue only has to
declare a scope for its new Tool rows via BUILTIN_TOOL_SCOPES below rather
than design its own gate. #189 (tools/builtin/channels.py) is the first
consumer.

A tool with no entry in BUILTIN_TOOL_SCOPES gets Tool.required_scope=None,
meaning "no scope needed" -- byte-for-byte the pre-#188 behavior of
"assigned via agent_tool -> usable". Only a tool whose required_scope is
set needs an explicit grant, via AgentToolScope, before an agent can
actually invoke it -- see tool_resolution.py's resolve_agent_tools.

Deliberately one flat "manage" verb per resource category rather than
finer read/write/create/delete verbs (#188's issue body floats this as a
"potentially" -- there's no concrete tool asking for that granularity
yet, and TOOL_SCOPES is just a frozenset of strings, so a sub-issue that
does need it can add e.g. "channels:read" alongside "channels:manage"
without any model or migration change).
"""

TOOL_SCOPES: frozenset[str] = frozenset(
    {
        "channels:manage",
        "agents_teams:manage",
        "mcp_servers:manage",
        "workflows:manage",
        "settings:manage",
    }
)

# Populated by each sub-issue (#189-#193) as it adds new builtin tools
# with real reach. #189 is the first: its five mutating channel tools all
# share one "channels:manage" scope (see tools/builtin/channels.py);
# list_channels is read-only and deliberately left out, the same way
# read_file/list_files carry no required_scope either.
#
# #190 (tools/builtin/agents_teams.py) is the second: nine mutating
# agent/team tools share one "agents_teams:manage" scope. This is the
# highest-reach category so far -- update_agent's tool_ids lets an agent
# reassign any agent's tool assignments, including its own -- but
# assignment alone still isn't eligibility: a reassigned tool with its own
# required_scope only actually resolves if the target agent separately
# holds that scope via AgentToolScope, which stays an owner-only grant
# (api/agents.py's set_agent_tool_scopes, gated by OwnerGrant) with no
# built-in tool exposing it. list_agents/list_teams are read-only and
# deliberately left out, same as list_channels.
#
# #191 (tools/builtin/mcp_servers.py) is the third: register_mcp_server/
# reconnect_mcp_server/delete_mcp_server share one "mcp_servers:manage"
# scope. Deliberately narrower than api/mcp_servers.py's full HTTP surface
# -- no built-in tool sets auth headers or a stdio server's env vars (both
# hold secret values with nowhere safe to travel except a model-generated
# tool argument), and register_mcp_server only offers streamable-http, not
# stdio (local subprocess execution, gated by a live owner session at the
# HTTP route, not just a standing grant) -- see tools/builtin/
# mcp_servers.py's module docstring for the full reasoning. list_mcp_servers
# is read-only and deliberately left out, same as list_channels/list_agents.
#
# #192 (tools/builtin/workflows.py) is the fourth: create_workflow/
# update_workflow/delete_workflow/publish_workflow/unpublish_workflow share
# one "workflows:manage" scope. Deliberately narrower than api/workflows.py's
# full HTTP surface -- only the workflow-level definition CRUD, not the
# node/connection/schedule/webhook graph-editing sub-surface #192's issue
# body calls out as large enough to warrant its own follow-up -- see
# tools/builtin/workflows.py's module docstring. list_workflows is
# read-only and deliberately left out, same as list_channels/list_agents/
# list_mcp_servers.
BUILTIN_TOOL_SCOPES: dict[str, str] = {
    "create_channel": "channels:manage",
    "update_channel": "channels:manage",
    "archive_channel": "channels:manage",
    "unarchive_channel": "channels:manage",
    "reorder_channels": "channels:manage",
    "create_agent": "agents_teams:manage",
    "update_agent": "agents_teams:manage",
    "delete_agent": "agents_teams:manage",
    "update_agent_routing_rules": "agents_teams:manage",
    "update_agent_peer_preference": "agents_teams:manage",
    "rollback_agent_version": "agents_teams:manage",
    "create_team": "agents_teams:manage",
    "update_team": "agents_teams:manage",
    "delete_team": "agents_teams:manage",
    "register_mcp_server": "mcp_servers:manage",
    "reconnect_mcp_server": "mcp_servers:manage",
    "delete_mcp_server": "mcp_servers:manage",
    "create_workflow": "workflows:manage",
    "update_workflow": "workflows:manage",
    "delete_workflow": "workflows:manage",
    "publish_workflow": "workflows:manage",
    "unpublish_workflow": "workflows:manage",
}
