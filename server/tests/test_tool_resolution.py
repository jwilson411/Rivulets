"""Agent tool resolution (FR-8.2) -- builtin/custom/mcp -> real agno tool
objects, plus the builtin-tool seeding that has to run before any of this
is reachable via the agent_tool join table."""

from pathlib import Path

from agno.tools.function import Function
from agno.tools.mcp import MCPTools
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.tool_resolution import resolve_agent_tools, seed_builtin_tools
from rivulets.db.models import Agent, AgentTool, MCPServer, Tool

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
