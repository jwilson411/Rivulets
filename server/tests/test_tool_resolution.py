"""Agent tool resolution (FR-8.2) -- builtin/custom/mcp -> real agno tool
objects, plus the builtin-tool seeding that has to run before any of this
is reachable via the agent_tool join table."""

from pathlib import Path

from agno.tools.function import Function
from agno.tools.mcp import MCPTools
from agno.tools.mcp.params import StreamableHTTPClientParams
from mcp import StdioServerParameters
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.tool_resolution import (
    is_builtin_tool_authorized,
    resolve_agent_tools,
    seed_builtin_tools,
)
from rivulets.db.models import Agent, AgentTool, AgentToolScope, MCPServer, Tool

_AGENT_FIELDS = {
    "description": "A test agent for tool resolution tests.",
    "instructions": "Do the thing.",
    "model": "openai:gpt-4",
}

_CUSTOM_TOOL_SOURCE = '''
from agno.tools import tool


@tool
def greet(name: str) -> str:
    """Greets someone."""
    return f"Hello, {name}!"
'''


async def _make_agent(db: AsyncSession, agent_id: str = "agent-1") -> Agent:
    agent = Agent(id=agent_id, name=f"Agent {agent_id}", **_AGENT_FIELDS)
    db.add(agent)
    await db.commit()
    return agent


async def _assign(db: AsyncSession, agent_id: str, tool_id: str) -> None:
    db.add(AgentTool(agent_id=agent_id, tool_id=tool_id))
    await db.commit()


async def test_seed_builtin_tools_creates_rows(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)

    result = await db_session.execute(select(Tool).where(Tool.tool_type == "builtin"))
    names = {row.name for row in result.scalars().all()}
    assert "read_file" in names
    assert "web_search" in names


async def test_seed_builtin_tools_is_idempotent(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    await seed_builtin_tools(db_session)

    result = await db_session.execute(select(Tool).where(Tool.tool_type == "builtin"))
    names = [row.name for row in result.scalars().all()]
    assert len(names) == len(set(names))


async def test_resolve_agent_tools_returns_empty_for_agent_with_no_tools(
    db_session: AsyncSession,
) -> None:
    agent = await _make_agent(db_session)
    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_resolves_builtin(db_session: AsyncSession) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    tool_row = (await db_session.execute(select(Tool).where(Tool.name == "read_file"))).scalar_one()
    await _assign(db_session, agent.id, tool_row.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], Function)
    assert resolved[0].name == "read_file"


async def test_resolve_agent_tools_skips_tool_with_ungranted_scope(
    db_session: AsyncSession,
) -> None:
    """#188: a tool with a required_scope set doesn't resolve for an agent
    that hasn't been granted that scope via AgentToolScope, even though
    it's assigned via agent_tool."""
    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="scoped_tool",
        description="A tool with real reach.",
        tool_type="builtin",
        required_scope="channels:manage",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_resolves_tool_with_granted_scope(
    db_session: AsyncSession,
) -> None:
    """The counterpart to the skip test above: granting the required scope
    via AgentToolScope makes the assigned tool resolve normally."""
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    read_file_row = (
        await db_session.execute(select(Tool).where(Tool.name == "read_file"))
    ).scalar_one()
    read_file_row.required_scope = "channels:manage"
    await db_session.commit()
    await _assign(db_session, agent.id, read_file_row.id)
    db_session.add(AgentToolScope(agent_id=agent.id, scope="channels:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], Function)
    assert resolved[0].name == "read_file"


async def test_seed_builtin_tools_sets_channels_manage_scope(db_session: AsyncSession) -> None:
    """#189: create_channel is the first real (non-synthetic) consumer of
    BUILTIN_TOOL_SCOPES -- confirms seeding actually wires it up, not just
    the generic required_scope mechanism exercised above with a hand-built
    Tool row."""
    await seed_builtin_tools(db_session)

    create_channel_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_channel"))
    ).scalar_one()
    assert create_channel_row.required_scope == "channels:manage"

    list_channels_row = (
        await db_session.execute(select(Tool).where(Tool.name == "list_channels"))
    ).scalar_one()
    assert list_channels_row.required_scope is None


async def test_create_channel_tool_skipped_without_channels_manage_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    create_channel_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_channel"))
    ).scalar_one()
    await _assign(db_session, agent.id, create_channel_row.id)

    assert await resolve_agent_tools(db_session, agent) == []

    db_session.add(AgentToolScope(agent_id=agent.id, scope="channels:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)
    assert [fn.name for fn in resolved] == ["create_channel"]


async def test_seed_builtin_tools_sets_agents_teams_manage_scope(db_session: AsyncSession) -> None:
    """#190: create_agent is the second real consumer of BUILTIN_TOOL_SCOPES
    (after #189's channel tools) -- confirms seeding wires up
    "agents_teams:manage" for the mutating agent/team tools while leaving
    the read-only list_agents/list_teams unscoped, same shape as
    test_seed_builtin_tools_sets_channels_manage_scope above."""
    await seed_builtin_tools(db_session)

    create_agent_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_agent"))
    ).scalar_one()
    assert create_agent_row.required_scope == "agents_teams:manage"

    delete_team_row = (
        await db_session.execute(select(Tool).where(Tool.name == "delete_team"))
    ).scalar_one()
    assert delete_team_row.required_scope == "agents_teams:manage"

    list_agents_row = (
        await db_session.execute(select(Tool).where(Tool.name == "list_agents"))
    ).scalar_one()
    assert list_agents_row.required_scope is None

    list_teams_row = (
        await db_session.execute(select(Tool).where(Tool.name == "list_teams"))
    ).scalar_one()
    assert list_teams_row.required_scope is None


async def test_update_agent_tool_skipped_without_agents_teams_manage_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    update_agent_row = (
        await db_session.execute(select(Tool).where(Tool.name == "update_agent"))
    ).scalar_one()
    await _assign(db_session, agent.id, update_agent_row.id)

    assert await resolve_agent_tools(db_session, agent) == []

    db_session.add(AgentToolScope(agent_id=agent.id, scope="agents_teams:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)
    assert [fn.name for fn in resolved] == ["update_agent"]


async def test_seed_builtin_tools_sets_mcp_servers_manage_scope(db_session: AsyncSession) -> None:
    """#191: register_mcp_server/reconnect_mcp_server/delete_mcp_server are
    the third real consumer of BUILTIN_TOOL_SCOPES -- confirms seeding
    wires up "mcp_servers:manage" for the three mutating tools while
    leaving the read-only list_mcp_servers unscoped, same shape as
    test_seed_builtin_tools_sets_channels_manage_scope above."""
    await seed_builtin_tools(db_session)

    register_row = (
        await db_session.execute(select(Tool).where(Tool.name == "register_mcp_server"))
    ).scalar_one()
    assert register_row.required_scope == "mcp_servers:manage"

    reconnect_row = (
        await db_session.execute(select(Tool).where(Tool.name == "reconnect_mcp_server"))
    ).scalar_one()
    assert reconnect_row.required_scope == "mcp_servers:manage"

    delete_row = (
        await db_session.execute(select(Tool).where(Tool.name == "delete_mcp_server"))
    ).scalar_one()
    assert delete_row.required_scope == "mcp_servers:manage"

    list_row = (
        await db_session.execute(select(Tool).where(Tool.name == "list_mcp_servers"))
    ).scalar_one()
    assert list_row.required_scope is None


async def test_register_mcp_server_tool_skipped_without_mcp_servers_manage_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    register_row = (
        await db_session.execute(select(Tool).where(Tool.name == "register_mcp_server"))
    ).scalar_one()
    await _assign(db_session, agent.id, register_row.id)

    assert await resolve_agent_tools(db_session, agent) == []

    db_session.add(AgentToolScope(agent_id=agent.id, scope="mcp_servers:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)
    assert [fn.name for fn in resolved] == ["register_mcp_server"]


async def test_seed_builtin_tools_sets_settings_manage_scope(db_session: AsyncSession) -> None:
    """#193: get_workspace_settings/update_workspace_settings are the
    fifth real consumer of BUILTIN_TOOL_SCOPES -- confirms seeding wires
    up "settings:manage" for both. Unlike every prior category's list
    tool, get_workspace_settings is scoped too (api/settings.py's GET
    route is OwnerGrant-gated same as its PATCH) -- see tool_scopes.py's
    BUILTIN_TOOL_SCOPES comment for why."""
    await seed_builtin_tools(db_session)

    get_row = (
        await db_session.execute(select(Tool).where(Tool.name == "get_workspace_settings"))
    ).scalar_one()
    assert get_row.required_scope == "settings:manage"

    update_row = (
        await db_session.execute(select(Tool).where(Tool.name == "update_workspace_settings"))
    ).scalar_one()
    assert update_row.required_scope == "settings:manage"

    folder_row = (
        await db_session.execute(select(Tool).where(Tool.name == "set_working_directory"))
    ).scalar_one()
    assert folder_row.required_scope == "settings:manage"


async def test_update_workspace_settings_tool_skipped_without_settings_manage_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    update_row = (
        await db_session.execute(select(Tool).where(Tool.name == "update_workspace_settings"))
    ).scalar_one()
    await _assign(db_session, agent.id, update_row.id)

    assert await resolve_agent_tools(db_session, agent) == []

    db_session.add(AgentToolScope(agent_id=agent.id, scope="settings:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)
    assert [fn.name for fn in resolved] == ["update_workspace_settings"]


async def test_seed_builtin_tools_sets_invites_manage_scope(db_session: AsyncSession) -> None:
    """#193: create_invite/list_invites/revoke_invite share one
    "invites:manage" scope, separate from "settings:manage" -- an invite
    is a more security-sensitive resource than a settings key/value pair
    (tool_scopes.py's BUILTIN_TOOL_SCOPES comment)."""
    await seed_builtin_tools(db_session)

    create_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_invite"))
    ).scalar_one()
    assert create_row.required_scope == "invites:manage"

    list_row = (
        await db_session.execute(select(Tool).where(Tool.name == "list_invites"))
    ).scalar_one()
    assert list_row.required_scope == "invites:manage"

    revoke_row = (
        await db_session.execute(select(Tool).where(Tool.name == "revoke_invite"))
    ).scalar_one()
    assert revoke_row.required_scope == "invites:manage"


async def test_create_invite_tool_skipped_without_invites_manage_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    create_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_invite"))
    ).scalar_one()
    await _assign(db_session, agent.id, create_row.id)

    assert await resolve_agent_tools(db_session, agent) == []

    db_session.add(AgentToolScope(agent_id=agent.id, scope="invites:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)
    assert [fn.name for fn in resolved] == ["create_invite"]


async def test_seed_builtin_tools_sets_sensitive_tools_manage_scope(
    db_session: AsyncSession,
) -> None:
    """#240: execute_python/http_request/write_file/query_workspace_db
    (SENSITIVE_BUILTIN_TOOL_NAMES) previously carried no required_scope at
    all, so update_agent's tool_ids (gated only by "agents_teams:manage")
    could assign them to any agent and they'd resolve immediately -- no
    separate owner-only grant the way every other category requires. They
    now share one "sensitive_tools:manage" scope, same shape as
    test_seed_builtin_tools_sets_channels_manage_scope above."""
    await seed_builtin_tools(db_session)

    for name in (
        "execute_python",
        "fetch_webpage",
        "http_request",
        "write_file",
        "query_workspace_db",
    ):
        row = (await db_session.execute(select(Tool).where(Tool.name == name))).scalar_one()
        assert row.required_scope == "sensitive_tools:manage"


async def test_seed_builtin_tools_sets_integrations_google_scope(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)

    for name in (
        "google_gmail_search",
        "google_gmail_read",
        "google_gmail_draft",
        "google_gmail_send",
        "google_calendar_list",
        "google_calendar_create",
        "google_calendar_update",
        "google_drive_search",
        "google_drive_read",
        "google_drive_write",
        "google_docs_read",
        "google_docs_append",
        "google_sheets_read",
        "google_sheets_update",
    ):
        row = (await db_session.execute(select(Tool).where(Tool.name == name))).scalar_one()
        assert row.required_scope == "integrations:google"


async def test_execute_python_tool_skipped_without_sensitive_tools_manage_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    execute_python_row = (
        await db_session.execute(select(Tool).where(Tool.name == "execute_python"))
    ).scalar_one()
    await _assign(db_session, agent.id, execute_python_row.id)

    assert await resolve_agent_tools(db_session, agent) == []

    db_session.add(AgentToolScope(agent_id=agent.id, scope="sensitive_tools:manage"))
    await db_session.commit()

    resolved = await resolve_agent_tools(db_session, agent)
    assert [fn.name for fn in resolved] == ["execute_python"]


async def test_is_builtin_tool_authorized_true_for_unscoped_assigned_builtin(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    read_file_row = (
        await db_session.execute(select(Tool).where(Tool.name == "read_file"))
    ).scalar_one()
    await _assign(db_session, agent.id, read_file_row.id)

    assert await is_builtin_tool_authorized(db_session, agent, "read_file") is True


async def test_is_builtin_tool_authorized_false_when_not_assigned(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)

    assert await is_builtin_tool_authorized(db_session, agent, "create_invite") is False


async def test_is_builtin_tool_authorized_false_for_scoped_tool_without_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    create_invite_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_invite"))
    ).scalar_one()
    await _assign(db_session, agent.id, create_invite_row.id)

    assert await is_builtin_tool_authorized(db_session, agent, "create_invite") is False


async def test_is_builtin_tool_authorized_true_for_scoped_tool_with_grant(
    db_session: AsyncSession,
) -> None:
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    create_invite_row = (
        await db_session.execute(select(Tool).where(Tool.name == "create_invite"))
    ).scalar_one()
    await _assign(db_session, agent.id, create_invite_row.id)
    db_session.add(AgentToolScope(agent_id=agent.id, scope="invites:manage"))
    await db_session.commit()

    assert await is_builtin_tool_authorized(db_session, agent, "create_invite") is True


async def test_is_builtin_tool_authorized_false_for_name_colliding_custom_tool(
    db_session: AsyncSession,
) -> None:
    """#240's core scenario: an agent has a *custom* tool literally named
    "create_invite" assigned (no scope needed -- custom tools carry no
    required_scope), and never has the real builtin "create_invite" (nor
    "invites:manage") at all. dispatch/service.py's trigger handlers used
    to fire the real mutator off `tool_call.tool_name == "create_invite"`
    alone, with nothing distinguishing this from a legitimate call --
    is_builtin_tool_authorized must refuse it because the assigned row
    backing that name isn't tool_type='builtin'."""
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)
    fake_create_invite = Tool(
        name="create_invite",
        description="A custom tool that happens to share a sensitive builtin's name.",
        tool_type="custom",
        source_path="/does/not/matter.py",
    )
    db_session.add(fake_create_invite)
    await db_session.commit()
    await _assign(db_session, agent.id, fake_create_invite.id)

    assert await is_builtin_tool_authorized(db_session, agent, "create_invite") is False


async def test_resolve_agent_tools_skips_unknown_builtin(db_session: AsyncSession) -> None:
    agent = await _make_agent(db_session)
    tool_row = Tool(name="not_a_real_builtin", description="stale", tool_type="builtin")
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_resolves_custom(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    source_file = tmp_path / "greet_tool.py"
    source_file.write_text(_CUSTOM_TOOL_SOURCE)

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="greet",
        description="Greets someone.",
        tool_type="custom",
        source_path=str(source_file),
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], Function)
    assert resolved[0].name == "greet"
    assert resolved[0].entrypoint is not None
    assert resolved[0].entrypoint("World") == "Hello, World!"


async def test_resolve_agent_tools_skips_custom_with_missing_file(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="ghost",
        description="File was deleted.",
        tool_type="custom",
        source_path=str(tmp_path / "does-not-exist.py"),
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_skips_custom_with_no_source_path(
    db_session: AsyncSession,
) -> None:
    """The create()-then-never-edited state -- no "save from editor"
    endpoint exists yet, so a freshly created custom tool has no file."""
    agent = await _make_agent(db_session)
    tool_row = Tool(name="empty", description="Never edited.", tool_type="custom")
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_skips_custom_when_function_name_mismatched(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    source_file = tmp_path / "mismatched.py"
    source_file.write_text(_CUSTOM_TOOL_SOURCE)

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="wrong_name",
        description="Doesn't match the @tool function's name.",
        tool_type="custom",
        source_path=str(source_file),
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_resolves_mcp(db_session: AsyncSession) -> None:
    server = MCPServer(name="test-server", url="http://localhost:9999/mcp")
    db_session.add(server)
    await db_session.commit()

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="remote_tool",
        description="An MCP tool.",
        tool_type="mcp",
        mcp_server_id=server.id,
        mcp_tool_name="remote_tool",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], MCPTools)
    assert resolved[0].initialized is False


async def test_resolve_agent_tools_resolves_mcp_with_headers(db_session: AsyncSession) -> None:
    """#124: a server with stored auth headers must build MCPTools with
    server_params carrying them, not the plain url=/no-auth form -- the
    runtime tool-call path, distinct from (and previously missed by)
    discovery-time resolution in api/mcp_servers.py."""
    import json

    from rivulets.agentos.mcp import mcp_header_ref
    from rivulets.security.credentials import store_secret

    server = MCPServer(
        name="test-server",
        url="http://localhost:9999/mcp",
        header_names_json=json.dumps(["Authorization"]),
    )
    db_session.add(server)
    await db_session.commit()
    store_secret(mcp_header_ref(server.id), json.dumps({"Authorization": "Bearer sk-real"}))

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="remote_tool",
        description="An MCP tool.",
        tool_type="mcp",
        mcp_server_id=server.id,
        mcp_tool_name="remote_tool",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], MCPTools)
    server_params = resolved[0].server_params
    assert isinstance(server_params, StreamableHTTPClientParams)
    assert server_params.headers == {"Authorization": "Bearer sk-real"}


async def test_resolve_agent_tools_resolves_stdio_mcp(db_session: AsyncSession) -> None:
    """#187: a stdio-transport server must build MCPTools with a
    shlex-joined `command` string and transport="stdio", not the
    url=/streamable-http form."""
    import json

    server = MCPServer(
        name="local-server",
        transport="stdio",
        command="npx",
        args_json=json.dumps(["-y", "@modelcontextprotocol/server-filesystem", "/data"]),
    )
    db_session.add(server)
    await db_session.commit()

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="list_dir",
        description="An MCP tool.",
        tool_type="mcp",
        mcp_server_id=server.id,
        mcp_tool_name="list_dir",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], MCPTools)
    server_params = resolved[0].server_params
    assert isinstance(server_params, StdioServerParameters)
    assert server_params.command == "npx"
    assert server_params.args == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/data",
    ]


async def test_resolve_agent_tools_resolves_stdio_mcp_with_env(db_session: AsyncSession) -> None:
    import json

    from rivulets.agentos.mcp import mcp_env_ref
    from rivulets.security.credentials import store_secret

    server = MCPServer(
        name="local-server",
        transport="stdio",
        command="npx",
        env_names_json=json.dumps(["API_KEY"]),
    )
    db_session.add(server)
    await db_session.commit()
    store_secret(mcp_env_ref(server.id), json.dumps({"API_KEY": "sk-real"}))

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="list_dir",
        description="An MCP tool.",
        tool_type="mcp",
        mcp_server_id=server.id,
        mcp_tool_name="list_dir",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert isinstance(resolved[0], MCPTools)
    server_params = resolved[0].server_params
    assert isinstance(server_params, StdioServerParameters)
    assert server_params.env is not None
    assert server_params.env["API_KEY"] == "sk-real"


async def test_resolve_agent_tools_skips_stdio_mcp_with_disallowed_command(
    db_session: AsyncSession,
) -> None:
    """A stored command that fails agno's own prepare_command() validation
    (e.g. an executable outside its allowlist) must be skipped like any
    other broken tool assignment (NFR-2.4), not take the whole agent
    resolution down."""
    server = MCPServer(name="local-server", transport="stdio", command="not-a-real-executable-xyz")
    db_session.add(server)
    await db_session.commit()

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="bad_tool",
        description="An MCP tool with a disallowed command.",
        tool_type="mcp",
        mcp_server_id=server.id,
        mcp_tool_name="bad_tool",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_skips_mcp_with_missing_server(
    db_session: AsyncSession,
) -> None:
    """A tool_type='mcp' row referencing a server that no longer exists --
    the app's own delete-server flow (api/mcp_servers.py) always deletes
    dependent tool rows first, so this can only arise from a stale
    reference (e.g. a sync message applied out of order); simulate it by
    bypassing FK enforcement for the server delete, same as such a
    reference would actually appear."""
    server = MCPServer(name="test-server", url="http://localhost:9999/mcp")
    db_session.add(server)
    await db_session.commit()

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="orphaned",
        description="Server was deleted.",
        tool_type="mcp",
        mcp_server_id=server.id,
        mcp_tool_name="whatever",
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    await db_session.execute(text("PRAGMA foreign_keys=OFF"))
    await db_session.execute(delete(MCPServer).where(MCPServer.id == server.id))
    await db_session.commit()
    await db_session.execute(text("PRAGMA foreign_keys=ON"))

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_skips_mcp_missing_tool_name(
    db_session: AsyncSession,
) -> None:
    server = MCPServer(name="test-server", url="http://localhost:9999/mcp")
    db_session.add(server)
    await db_session.commit()

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="incomplete",
        description="No mcp_tool_name set.",
        tool_type="mcp",
        mcp_server_id=server.id,
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_continues_past_one_broken_tool(
    db_session: AsyncSession,
) -> None:
    """NFR-2.4: one broken tool assignment must not take the whole agent
    offline -- the other, healthy tool must still resolve."""
    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)

    broken = Tool(
        name="broken",
        description="Missing file.",
        tool_type="custom",
        source_path="/does/not/exist.py",
    )
    db_session.add(broken)
    healthy = (await db_session.execute(select(Tool).where(Tool.name == "read_file"))).scalar_one()
    await db_session.commit()
    await _assign(db_session, agent.id, broken.id)
    await _assign(db_session, agent.id, healthy.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert resolved[0].name == "read_file"


async def test_resolve_agent_tools_skips_custom_tool_that_raises_during_exec(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Distinct from a missing file or a name mismatch (both handled by
    _load_custom_tool returning None): a file that exists and is
    syntactically valid but raises while its top-level code actually runs
    (e.g. an import of something not installed). exec_module doesn't
    catch this itself -- resolve_agent_tools's per-tool try/except must."""
    source_file = tmp_path / "broken_at_runtime.py"
    source_file.write_text("import this_module_does_not_exist\n")

    agent = await _make_agent(db_session)
    tool_row = Tool(
        name="broken_at_runtime",
        description="Raises when loaded, not when saved.",
        tool_type="custom",
        source_path=str(source_file),
    )
    db_session.add(tool_row)
    await db_session.commit()
    await _assign(db_session, agent.id, tool_row.id)

    assert await resolve_agent_tools(db_session, agent) == []


async def test_resolve_agent_tools_continues_past_a_tool_that_raises_during_exec(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Same NFR-2.4 guarantee as test_resolve_agent_tools_continues_past_one_broken_tool,
    but for the exec-time-exception path specifically, not a missing file."""
    source_file = tmp_path / "broken_at_runtime.py"
    source_file.write_text("import this_module_does_not_exist\n")

    await seed_builtin_tools(db_session)
    agent = await _make_agent(db_session)

    broken = Tool(
        name="broken_at_runtime",
        description="Raises when loaded.",
        tool_type="custom",
        source_path=str(source_file),
    )
    db_session.add(broken)
    healthy = (await db_session.execute(select(Tool).where(Tool.name == "web_search"))).scalar_one()
    await db_session.commit()
    await _assign(db_session, agent.id, broken.id)
    await _assign(db_session, agent.id, healthy.id)

    resolved = await resolve_agent_tools(db_session, agent)

    assert len(resolved) == 1
    assert resolved[0].name == "web_search"
