"""Built-in tool library (FR-8.1). Each module exports an agno `@tool`-decorated
callable; api/tools.py registers the ones a workspace enables against agents.

`handoff` is the exception — it's not opt-in like the rest of this library;
agentos/service.py attaches it to every agent unconditionally (FR-6.1).
"""

from rivulets.tools.builtin.code_exec import execute_python
from rivulets.tools.builtin.db_query import query_workspace_db
from rivulets.tools.builtin.files import read_attached_file
from rivulets.tools.builtin.filesystem import list_files, read_file, write_file
from rivulets.tools.builtin.handoff import handoff
from rivulets.tools.builtin.http_request import http_request
from rivulets.tools.builtin.knowledge_base import search_knowledge_base
from rivulets.tools.builtin.run_workflow import run_workflow
from rivulets.tools.builtin.schedules import cancel_schedule, list_schedules, schedule_workflow
from rivulets.tools.builtin.web_search import web_search

__all__ = [
    "cancel_schedule",
    "execute_python",
    "handoff",
    "http_request",
    "list_files",
    "list_schedules",
    "query_workspace_db",
    "read_attached_file",
    "read_file",
    "run_workflow",
    "schedule_workflow",
    "search_knowledge_base",
    "web_search",
    "write_file",
]
