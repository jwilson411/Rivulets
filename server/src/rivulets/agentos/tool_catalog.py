"""Human-facing labels and grouping for the agent Tools picker (#422).

Function names stay snake_case (they're Python identifiers and the
agent-facing tool names). The picker was rendering those raw, so
granting `http_request` vs `web_search` was guesswork. Display names and
groups are derived here, not stored on Tool -- custom/MCP names are
user-chosen identifiers, and the builtin set is fixed in
tool_resolution.py's registry.

Groups match the issue's buckets: Chat (conversation helpers), Files
(sandbox filesystem + code exec), Workspace admin (everything that
mutates workspace structure). Custom and MCP tools get their own groups
so they don't land in Workspace admin by default.
"""

from __future__ import annotations

# Tokens that look wrong when title-cased from snake_case ("Http", "Mcp",
# "Db"). Everything else is first-word capitalized, rest lower.
_TOKEN_OVERRIDES: dict[str, str] = {
    "db": "DB",
    "http": "HTTP",
    "mcp": "MCP",
    "python": "Python",
}

# Conversation-time helpers -- the set the issue's repro is about
# (http_request vs web_search) plus the other read-oriented chat tools.
CHAT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "http_request",
        "read_attached_file",
        "search_knowledge_base",
        "web_search",
    }
)

# Workspace-sandbox filesystem plus the code-exec tool that writes there.
FILES_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "execute_python",
        "list_files",
        "read_file",
        "write_file",
    }
)

TOOL_GROUP_CHAT = "chat"
TOOL_GROUP_FILES = "files"
TOOL_GROUP_WORKSPACE_ADMIN = "workspace_admin"
TOOL_GROUP_CUSTOM = "custom"
TOOL_GROUP_MCP = "mcp"


def display_name_for(name: str) -> str:
    """Turn `update_agent_peer_preference` into 'Update agent peer preference'.

    Acronyms in `_TOKEN_OVERRIDES` keep their usual casing so `http_request`
    is 'HTTP request', not 'Http request'.
    """
    words: list[str] = []
    for index, part in enumerate(name.split("_")):
        if not part:
            continue
        override = _TOKEN_OVERRIDES.get(part.lower())
        if override is not None:
            words.append(override)
        elif index == 0:
            words.append(part[:1].upper() + part[1:].lower())
        else:
            words.append(part.lower())
    return " ".join(words) or name


def group_for(name: str, tool_type: str) -> str:
    if tool_type == "custom":
        return TOOL_GROUP_CUSTOM
    if tool_type == "mcp":
        return TOOL_GROUP_MCP
    if name in CHAT_TOOL_NAMES:
        return TOOL_GROUP_CHAT
    if name in FILES_TOOL_NAMES:
        return TOOL_GROUP_FILES
    return TOOL_GROUP_WORKSPACE_ADMIN


def one_line_description(description: str) -> str:
    """First sentence of a docstring, with wrapping collapsed.

    Builtin tool descriptions are function docstrings and often wrap
    mid-sentence; taking the first physical line would cut them off.
    """
    trimmed = description.strip()
    if not trimmed:
        return ""
    collapsed = " ".join(line.strip() for line in trimmed.splitlines() if line.strip())
    for index, char in enumerate(collapsed):
        if char in ".!?" and (index + 1 == len(collapsed) or collapsed[index + 1].isspace()):
            return collapsed[: index + 1]
    return collapsed
