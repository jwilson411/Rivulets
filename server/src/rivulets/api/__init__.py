"""App Server REST API, mounted at /api/v1.

The UI never talks to AgentOS directly — every request here either serves
App Server state (channels, teams, agents, rivulets, tools, settings...) or
proxies to the local AgentOS instance. This module just aggregates the
per-resource routers; each one owns its own path prefix and schemas.
"""

from fastapi import APIRouter

from rivulets.api import (
    agents,
    approvals,
    auth,
    backups,
    budgets,
    channels,
    dispatch,
    evals,
    files,
    health,
    humans,
    integrations,
    invites,
    knowledge_bases,
    mcp_servers,
    providers,
    rivulets,
    runs,
    sync,
    teams,
    tools,
    update,
    usage,
    webhooks,
    workflows,
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
api_router.include_router(rivulets.router)
api_router.include_router(humans.router)
api_router.include_router(invites.router)
api_router.include_router(tools.router)
api_router.include_router(mcp_servers.router)
api_router.include_router(files.router)
api_router.include_router(settings_routes.router)
api_router.include_router(sync.router)
api_router.include_router(providers.router)
api_router.include_router(integrations.router)
api_router.include_router(backups.router)
api_router.include_router(usage.router)
api_router.include_router(dispatch.router)
api_router.include_router(update.router)
api_router.include_router(workflows.router)
api_router.include_router(webhooks.router)
api_router.include_router(evals.router)
api_router.include_router(budgets.router)
api_router.include_router(runs.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(approvals.router)

__all__ = ["api_router"]
