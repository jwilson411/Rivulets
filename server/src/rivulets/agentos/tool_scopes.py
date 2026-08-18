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
        "invites:manage",
        "sensitive_tools:manage",
        "integrations:google",
        "integrations:google:write",
    }
)

# Connecting Google + the read scope must not enable send-as-me in chat
# (#463). Starter agents, New agent, and hire_teammate grant every other
# catalog scope; write stays off until an owner checks it on that agent.
DEFAULT_WITHHELD_SCOPES: frozenset[str] = frozenset({"integrations:google:write"})
DEFAULT_AGENT_SCOPES: frozenset[str] = TOOL_SCOPES - DEFAULT_WITHHELD_SCOPES

# Populated by each sub-issue (#189-#193) as it adds new builtin tools
# with real reach. #189 is the first: its five mutating channel tools all
# share one "channels:manage" scope (see tools/builtin/channels.py);
# list_channels is read-only and deliberately left out, the same way
# read_file/list_files carry no required_scope either.
#
# #190 (tools/builtin/agents_teams.py) is the second: nine mutating
# agent/team tools share one "agents_teams:manage" scope. This is the
# highest-reach category so far -- update_agent's tool_ids lets an agent
# reassign any agent's tool assignments, including its own. For a builtin
# tool with its own required_scope, assignment alone still isn't
# eligibility: it only actually resolves if the target agent separately
# holds that scope via AgentToolScope, which stays an owner-only grant
# (api/agents.py's set_agent_tool_scopes, gated by OwnerGrant) with no
# built-in tool exposing it. That does NOT extend to custom or MCP tools
# (#310 corrected an earlier version of this comment that implied it did)
# -- those have no required_scope field to gate on, so assignment *is*
# eligibility: a custom tool runs unsandboxed the moment it's called, an
# MCP tool resolves with the owner's stored headers/env. update_agent's
# dispatch handler (dispatch/service.py's _handle_update_agent_trigger)
# refuses to assign a custom/MCP/required_scope/SENSITIVE_BUILTIN_TOOL_NAMES
# tool at all via tool_ids, rather than relying on this scope to bound it.
# list_agents/list_teams are read-only and deliberately left out, same as
# list_channels.
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
#
# #240: SENSITIVE_BUILTIN_TOOL_NAMES (agentos/tool_resolution.py --
# execute_python, fetch_webpage, http_request, write_file,
# query_workspace_db) previously
# carried no required_scope at all, which oversold "agents_teams:manage":
# update_agent's tool_ids lets an agent reassign *any* tool to *any*
# agent, and with no scope of their own, these four resolved and ran the
# instant they were assigned -- no separate owner-only step the way every
# category above requires. "sensitive_tools:manage" closes that: assigning
# one of these four via tool_ids is still possible (agents_teams:manage
# alone permits it), but it stays inert for the target agent until an
# owner separately grants sensitive_tools:manage to it, the same
# assignment-isn't-eligibility split every other scope already enforces.
# A single shared scope, not one each -- these four aren't independently
# grantable resources like channels/agents/invites, they're one severity
# tier ("arbitrary code exec, arbitrary outbound HTTP, local filesystem
# writes, DB access" -- tool_resolution.py's own description) that either
# is or isn't the right amount of trust to place in a given agent. Folded
# into BUILTIN_TOOL_SCOPES below (not a separate dict) since that's the
# only mapping seed_builtin_tools and resolve_agent_tools actually read.
#
# #193 (tools/builtin/settings.py, tools/builtin/invites.py) is the fifth
# and last of #125's originally-scoped sub-issues, and the first to cover
# two distinct resource categories at once -- api/settings.py and api/
# invites.py are separate routers, each entirely OwnerGrant-gated
# (unlike every category above, where only the *mutating* routes were
# owner-gated and reads were open to any session). That's why, uniquely
# here, the read-only tools (get_workspace_settings, list_invites) are
# scoped too, rather than left unscoped like list_channels/list_agents/
# list_mcp_servers/list_workflows -- an agent with no grant at all
# shouldn't be able to read workspace settings or enumerate outstanding
# invites any more than it could through the HTTP API. get_workspace_settings/
# update_workspace_settings share "settings:manage"; create_invite/
# list_invites/revoke_invite share "invites:manage" -- a separate scope
# because an invite is a distinct, more security-sensitive resource than a
# settings key/value pair (it's a credential capable of admitting a new
# human to the workspace), so a workspace owner may want to grant one
# without the other.
#
# #458 / #463: Google read tools (search / read / list) share
# "integrations:google". Write tools (draft / send / create) need a
# second owner grant, "integrations:google:write", so connecting an
# account only enables read until send/create is turned on for a
# specific agent. Write tools are also marked sensitive so unattended
# use still goes through tool_audit.py.
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
    "get_workspace_settings": "settings:manage",
    "update_workspace_settings": "settings:manage",
    "set_working_directory": "settings:manage",
    "create_invite": "invites:manage",
    "list_invites": "invites:manage",
    "revoke_invite": "invites:manage",
    "execute_python": "sensitive_tools:manage",
    "fetch_webpage": "sensitive_tools:manage",
    "http_request": "sensitive_tools:manage",
    "write_file": "sensitive_tools:manage",
    "query_workspace_db": "sensitive_tools:manage",
    "google_gmail_search": "integrations:google",
    "google_gmail_read": "integrations:google",
    "google_gmail_draft": "integrations:google:write",
    "google_gmail_send": "integrations:google:write",
    "google_calendar_list": "integrations:google",
    "google_calendar_create": "integrations:google:write",
}
