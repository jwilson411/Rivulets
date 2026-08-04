"""MCP server registration (FR-8.5).

Connecting to a server and discovering its tools goes through AgentOS's
MCP configuration (api-design.md#agentos-api-internal), which isn't wired
up yet — registration here just persists the row; `connected` stays False
until that integration exists.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_hive.api.deps import CurrentWorkspaceId, DbSession
from agent_hive.db.models import MCPServer

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


class MCPServerCreate(BaseModel):
    name: str
    url: str


class MCPServerOut(BaseModel):
    id: str
    name: str
    url: str
    connected: bool
    last_connected_at: str | None

    model_config = {"from_attributes": True}


async def _get_or_404(db: DbSession, server_id: str) -> MCPServer:
    server = await db.get(MCPServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    return server


@router.get("", response_model=list[MCPServerOut])
async def list_mcp_servers(db: DbSession, _: CurrentWorkspaceId) -> list[MCPServer]:
    result = await db.execute(select(MCPServer))
    return list(result.scalars().all())


@router.post("", response_model=MCPServerOut, status_code=status.HTTP_201_CREATED)
async def register_mcp_server(
    body: MCPServerCreate, db: DbSession, _: CurrentWorkspaceId
) -> MCPServer:
    server = MCPServer(name=body.name, url=body.url)
    db.add(server)
    # TODO(FR-8.5): connect, discover tools, register them in AgentOS,
    # and set server.connected / last_connected_at on success.
    await db.commit()
    await db.refresh(server)
    return server


@router.get("/{server_id}", response_model=MCPServerOut)
async def get_mcp_server(server_id: str, db: DbSession, _: CurrentWorkspaceId) -> MCPServer:
    return await _get_or_404(db, server_id)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_mcp_server(server_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    server = await _get_or_404(db, server_id)
    await db.delete(server)
    await db.commit()


@router.post("/{server_id}/reconnect", response_model=MCPServerOut)
async def reconnect_mcp_server(server_id: str, db: DbSession, _: CurrentWorkspaceId) -> MCPServer:
    await _get_or_404(db, server_id)
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED, "MCP connection/discovery not yet wired up"
    )
