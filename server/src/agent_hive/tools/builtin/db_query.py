"""Database Query built-in tool (FR-8.1).

Scoped to the workspace's own SQLite database, opened read-only and
restricted to SELECT statements. Agents get visibility into workspace
data (agents, channels, run history) without a path to mutate state
outside the CRUD API — that's a much larger trust boundary than "let an
LLM-generated query touch the DB Agent Hive's own consistency depends on".
"""

import sqlite3

from agno.tools import tool

from agent_hive.config import get_settings

_MAX_ROWS = 200


@tool
def query_workspace_db(sql: str) -> str:
    """Run a read-only SELECT query against the workspace SQLite database
    and return up to 200 rows as tab-separated text."""
    stripped = sql.strip().lower()
    if not stripped.startswith("select"):
        raise ValueError("Only SELECT statements are permitted")

    db_path = get_settings().db_path
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(_MAX_ROWS)

    lines = ["\t".join(columns)]
    lines.extend("\t".join(str(v) for v in row) for row in rows)
    return "\n".join(lines)
