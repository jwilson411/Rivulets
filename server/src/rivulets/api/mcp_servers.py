"""MCP server registration (FR-8.5).

Registering a server always persists the row — even if the connection
attempt fails, matching this app's established NFR-2.4 pattern for
unreachable external services elsewhere (agent providers, dispatcher
LLM). A server that failed to connect just comes back with
`connected: false` and no discovered tools; POST .../reconnect retries.

Also synced (FR-9.1) — name/url only, on registration. `connected` and
`last_connected_at` are per-node status, not synced (sync/apply.py's
MCP_SERVER_SPEC and module docstring); reconnect doesn't change name/url
so it never publishes. Discovered Tool rows aren't synced here either —
each node discovers its own by connecting to the (synced) url.

Custom auth headers (#124) are per-node too, for the same reason as
`connected`/`last_connected_at`: their values are secrets, and secrets
never enter a sync payload (NFR-3.3, same rule ProviderConfig.api_key_ref
follows). Values live in the keychain (security/credentials.py) via
agentos.mcp.get_server_headers/mcp_header_ref; only header *names* are
persisted on the row (MCPServer.header_names_json), and only ever
returned to the client -- never values, matching ProviderConfig never
returning api_key/api_key_ref. Setting/replacing headers is owner-gated
(api/deps.py's require_owner_grant docstring: "provider credentials,
backups, sync config, invite management itself, etc.") since it's
entering a credential on the workspace's behalf; registering/reading/
reconnecting/removing a server with no headers stays open to any grant,
unchanged from before this feature existed."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from rivulets.agentos.mcp import (
    MCPConnectionError,
    discover_tools,
    get_server_headers,
    mcp_header_ref,
)
from rivulets.api.deps import (
    CurrentWorkspaceId,
    DbSession,
    OwnerGrant,
    SessionClaims,
    get_session_claims,
)
from rivulets.db.base import utcnow_iso
from rivulets.db.models import MCPServer, Tool
from rivulets.security.credentials import delete_secret, store_secret
from rivulets.sync.publish import publish_current_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


class MCPServerCreate(BaseModel):
    name: str
    url: str
    # Auth headers (e.g. {"Authorization": "Bearer ..."}) for streamable-
    # HTTP servers that require them. Entering these requires an owner
    # session (module docstring) -- plain name/url registration doesn't.
    headers: dict[str, str] | None = None


class MCPServerHeadersUpdate(BaseModel):
    # Always a full replace, not a merge -- {} clears every header. This
    # is a dedicated endpoint specifically for setting headers, so unlike
    # MCPServerCreate.headers there's no "None means don't touch" case to
    # distinguish from "clear them".
    headers: dict[str, str]


class MCPServerOut(BaseModel):
    id: str
    name: str
    url: str
    connected: bool
    last_connected_at: str | None
    header_names: list[str]


class MCPToolOut(BaseModel):
    id: str
    name: str
    description: str
    mcp_tool_name: str | None
    input_schema: dict[str, Any]

    model_config = {"from_attributes": True}


class MCPServerDetailOut(MCPServerOut):
    tools: list[MCPToolOut]


async def _get_or_404(db: DbSession, server_id: str) -> MCPServer:
    server = await db.get(MCPServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    return server


def _header_names(server: MCPServer) -> list[str]:
    return json.loads(server.header_names_json) if server.header_names_json else []


def _to_out(server: MCPServer) -> MCPServerOut:
    return MCPServerOut(
        id=server.id,
        name=server.name,
        url=server.url,
        connected=server.connected,
        last_connected_at=server.last_connected_at,
        header_names=_header_names(server),
    )


async def _to_detail(db: DbSession, server: MCPServer) -> MCPServerDetailOut:
    result = await db.execute(select(Tool).where(Tool.mcp_server_id == server.id))
    tools = list(result.scalars().all())
    return MCPServerDetailOut(
        **_to_out(server).model_dump(),
        tools=[
            MCPToolOut(
                id=t.id,
                name=t.name,
                description=t.description,
                mcp_tool_name=t.mcp_tool_name,
                input_schema=json.loads(t.mcp_input_schema_json) if t.mcp_input_schema_json else {},
            )
            for t in tools
        ],
    )


def _set_headers(server: MCPServer, headers: dict[str, str] | None) -> None:
    """Store/replace/clear `server`'s auth headers. `None` or `{}` clears
    them (and the previously-stored secret, if any); a non-empty dict
    replaces the whole set. Doesn't commit -- callers own the
    transaction."""
    if headers:
        store_secret(mcp_header_ref(server.id), json.dumps(headers))
        server.header_names_json = json.dumps(sorted(headers.keys()))
    else:
        if server.header_names_json:
            delete_secret(mcp_header_ref(server.id))
        server.header_names_json = None


async def _connect_and_sync_tools(db: DbSession, server: MCPServer) -> None:
    """(Re)discover `server`'s tools and replace its Tool rows with the
    current set — used by both registration and /reconnect. Doesn't
    commit; callers own the transaction."""
    await db.execute(delete(Tool).where(Tool.mcp_server_id == server.id))
    try:
        discovered = await discover_tools(server.url, headers=get_server_headers(server))
    except MCPConnectionError:
        logger.warning(
            "Could not connect to MCP server %r at %s", server.name, server.url, exc_info=True
        )
        server.connected = False
        return

    server.connected = True
    server.last_connected_at = utcnow_iso()
    for discovered_tool in discovered:
        db.add(
            Tool(
                name=discovered_tool.name,
                description=discovered_tool.description,
                tool_type="mcp",
                mcp_server_id=server.id,
                mcp_tool_name=discovered_tool.name,
                mcp_input_schema_json=json.dumps(discovered_tool.input_schema),
            )
        )


@router.get("", response_model=list[MCPServerOut])
async def list_mcp_servers(db: DbSession, _: CurrentWorkspaceId) -> list[MCPServerOut]:
    result = await db.execute(select(MCPServer))
    return [_to_out(server) for server in result.scalars().all()]


@router.post("", response_model=MCPServerDetailOut, status_code=status.HTTP_201_CREATED)
async def register_mcp_server(
    body: MCPServerCreate,
    db: DbSession,
    _: CurrentWorkspaceId,
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
) -> MCPServerDetailOut:
    if body.headers and claims.grant != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner access required to set auth headers")
    server = MCPServer(name=body.name, url=body.url)
    db.add(server)
    await db.flush()  # populates server.id (uuid7 default), needed by _set_headers' keychain ref
    _set_headers(server, body.headers)
    await _connect_and_sync_tools(db, server)
    await db.commit()
    await db.refresh(server)
    await publish_current_state(db, "mcp_server", server.id)
    return await _to_detail(db, server)


@router.get("/{server_id}", response_model=MCPServerDetailOut)
async def get_mcp_server(
    server_id: str, db: DbSession, _: CurrentWorkspaceId
) -> MCPServerDetailOut:
    server = await _get_or_404(db, server_id)
    return await _to_detail(db, server)


@router.put("/{server_id}/headers", response_model=MCPServerDetailOut)
async def set_mcp_server_headers(
    server_id: str,
    body: MCPServerHeadersUpdate,
    db: DbSession,
    _: CurrentWorkspaceId,
    _o: OwnerGrant,
) -> MCPServerDetailOut:
    """Replace `server_id`'s auth headers wholesale (empty dict clears
    them), then reconnect so newly-added/changed/removed auth takes
    effect immediately rather than waiting for the next manual reconnect
    or restart. Always owner-gated (module docstring) -- unlike
    MCPServerCreate.headers, there's no headerless case here to leave
    open to any grant."""
    server = await _get_or_404(db, server_id)
    _set_headers(server, body.headers)
    await _connect_and_sync_tools(db, server)
    await db.commit()
    await db.refresh(server)
    return await _to_detail(db, server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_mcp_server(server_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    server = await _get_or_404(db, server_id)
    if server.header_names_json:
        delete_secret(mcp_header_ref(server.id))
    # No ON DELETE CASCADE on tool.mcp_server_id — clear its discovered
    # tools first or SQLite's FK enforcement (session.py enables it) rejects
    # the delete.
    await db.execute(delete(Tool).where(Tool.mcp_server_id == server_id))
    await db.delete(server)
    await db.commit()


@router.post("/{server_id}/reconnect", response_model=MCPServerDetailOut)
async def reconnect_mcp_server(
    server_id: str, db: DbSession, _: CurrentWorkspaceId
) -> MCPServerDetailOut:
    server = await _get_or_404(db, server_id)
    await _connect_and_sync_tools(db, server)
    await db.commit()
    await db.refresh(server)
    return await _to_detail(db, server)
