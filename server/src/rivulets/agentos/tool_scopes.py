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
BUILTIN_TOOL_SCOPES: dict[str, str] = {
    "create_channel": "channels:manage",
    "update_channel": "channels:manage",
    "archive_channel": "channels:manage",
    "unarchive_channel": "channels:manage",
    "reorder_channels": "channels:manage",
}
