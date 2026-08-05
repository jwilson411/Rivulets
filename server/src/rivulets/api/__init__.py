"""App Server REST API (docs/architecture/api-design.md), mounted at /api/v1.

The UI never talks to AgentOS directly — every request here either serves
App Server state (channels, teams, agents, threads, tools, settings...) or
proxies to the local AgentOS instance. This module just aggregates the
per-resource routers; each one owns its own path prefix and schemas.
"""

from fastapi import APIRouter

from rivulets.api import (
    agents,
    auth,
    channels,
    files,
    health,
    mcp_servers,
    providers,
    sync,
    teams,
    threads,
    tools,
)
from rivulets.api import (
    settings as settings_routes,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(channels.router)
api_router.include_router(teams.router)
api_router.include_router(agents.router)
api_router.include_router(threads.router)
api_router.include_router(tools.router)
api_router.include_router(mcp_servers.router)
api_router.include_router(files.router)
api_router.include_router(settings_routes.router)
api_router.include_router(sync.router)
api_router.include_router(providers.router)

__all__ = ["api_router"]
