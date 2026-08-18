"""Human labels and grouping for the agent Tools picker (#422)."""

from rivulets.agentos.tool_catalog import display_name_for, group_for, one_line_description


def test_display_name_title_cases_snake_case() -> None:
    assert display_name_for("web_search") == "Web search"
    assert display_name_for("update_agent_peer_preference") == "Update agent peer preference"


def test_display_name_keeps_acronyms() -> None:
    assert display_name_for("http_request") == "HTTP request"
    assert display_name_for("execute_python") == "Execute Python"
    assert display_name_for("query_workspace_db") == "Query workspace DB"
    assert display_name_for("list_mcp_servers") == "List MCP servers"


def test_group_for_splits_builtins_and_user_tools() -> None:
    assert group_for("web_search", "builtin") == "chat"
    assert group_for("http_request", "builtin") == "chat"
    assert group_for("read_file", "builtin") == "files"
    assert group_for("set_working_directory", "builtin") == "files"
    assert group_for("execute_python", "builtin") == "files"
    assert group_for("create_agent", "builtin") == "workspace_admin"
    assert group_for("cancel_schedule", "builtin") == "workspace_admin"
    assert group_for("fetch_notes", "custom") == "custom"
    assert group_for("whatever", "mcp") == "mcp"


def test_one_line_description_takes_the_first_sentence() -> None:
    assert (
        one_line_description(
            "Search the web via Brave Search and return titles, URLs, and snippets."
        )
        == "Search the web via Brave Search and return titles, URLs, and snippets."
    )
    assert (
        one_line_description(
            "Read the content of a file attached to a rivulet message, given its\n"
            "file_id (as returned by the file upload API). Text/JSON files are\n"
            "returned as text."
        )
        == "Read the content of a file attached to a rivulet message, given its "
        "file_id (as returned by the file upload API)."
    )
    assert one_line_description("") == ""
