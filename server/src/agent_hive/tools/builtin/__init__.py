"""Built-in tool library (FR-8.1). Each module exports an agno `@tool`-decorated
callable; api/tools.py registers the ones a workspace enables against agents.

`handoff` is the exception — it's not opt-in like the rest of this library;
agentos/service.py attaches it to every agent unconditionally (FR-6.1).
"""

from agent_hive.tools.builtin.code_exec import execute_python
from agent_hive.tools.builtin.db_query import query_workspace_db
from agent_hive.tools.builtin.files import read_attached_file
from agent_hive.tools.builtin.filesystem import list_files, read_file, write_file
from agent_hive.tools.builtin.handoff import handoff
from agent_hive.tools.builtin.http_request import http_request
from agent_hive.tools.builtin.web_search import web_search

__all__ = [
    "execute_python",
    "handoff",
    "http_request",
    "list_files",
    "query_workspace_db",
    "read_attached_file",
    "read_file",
    "web_search",
    "write_file",
]
