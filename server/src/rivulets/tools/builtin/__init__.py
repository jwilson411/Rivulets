"""Built-in tool library (FR-8.1). Each module exports an agno `@tool`-decorated
callable; api/tools.py registers the ones a workspace enables against agents.

`handoff` is the exception — it's not opt-in like the rest of this library;
agentos/service.py attaches it to every agent unconditionally (FR-6.1).
"""

from rivulets.tools.builtin.agents_teams import (
    create_agent,
    create_team,
    delete_agent,
    delete_team,
    list_agents,
    list_teams,
    rollback_agent_version,
    update_agent,
    update_agent_peer_preference,
    update_agent_routing_rules,
    update_team,
)
from rivulets.tools.builtin.channels import (
    archive_channel,
    create_channel,
    list_channels,
    reorder_channels,
    unarchive_channel,
    update_channel,
)
from rivulets.tools.builtin.code_exec import execute_python
from rivulets.tools.builtin.db_query import query_workspace_db
from rivulets.tools.builtin.files import read_attached_file
from rivulets.tools.builtin.filesystem import list_files, read_file, write_file
from rivulets.tools.builtin.handoff import handoff
from rivulets.tools.builtin.http_request import http_request
from rivulets.tools.builtin.knowledge_base import search_knowledge_base
from rivulets.tools.builtin.mcp_servers import (
    delete_mcp_server,
    list_mcp_servers,
    reconnect_mcp_server,
    register_mcp_server,
)
from rivulets.tools.builtin.run_workflow import run_workflow
from rivulets.tools.builtin.schedules import cancel_schedule, list_schedules, schedule_workflow
from rivulets.tools.builtin.web_search import web_search
from rivulets.tools.builtin.workflows import (
    create_workflow,
    delete_workflow,
    list_workflows,
    publish_workflow,
    unpublish_workflow,
    update_workflow,
)

__all__ = [
    "archive_channel",
    "cancel_schedule",
    "create_agent",
    "create_channel",
    "create_team",
    "create_workflow",
    "delete_agent",
    "delete_mcp_server",
    "delete_team",
    "delete_workflow",
    "execute_python",
    "handoff",
    "http_request",
    "list_agents",
    "list_channels",
    "list_files",
    "list_mcp_servers",
    "list_schedules",
    "list_teams",
    "list_workflows",
    "publish_workflow",
    "query_workspace_db",
    "read_attached_file",
    "read_file",
    "reconnect_mcp_server",
    "register_mcp_server",
    "reorder_channels",
    "rollback_agent_version",
    "run_workflow",
    "schedule_workflow",
    "search_knowledge_base",
    "unarchive_channel",
    "unpublish_workflow",
    "update_agent",
    "update_agent_peer_preference",
    "update_agent_routing_rules",
    "update_channel",
    "update_team",
    "update_workflow",
    "web_search",
    "write_file",
]
