"""FastAPI app factory (docs/architecture/overview-and-stack.md).

Security posture per NFR-3.4 / security-and-risks.md: same-origin only
(the UI and API share this process), no CORS, and a CSP that only trusts
this origin. The App Server binds to 127.0.0.1 — enforced in main.py, not
here, since that's a listen-address concern rather than an app concern.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from agent_hive.agentos import init_agentos, sync_agents
from agent_hive.api import api_router
from agent_hive.config import get_settings
from agent_hive.db.session import init_db, session_scope
from agent_hive.sync import get_sync_engine, init_sync_engine
from agent_hive.sync.apply import handle_incoming_state_change

_CSP = "default-src 'self'; script-src 'self'; connect-src 'self' http://localhost:8484"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await init_db()
    # Repopulate AgentOS's agent list from the DB on every startup — agents
    # created before a restart need to be re-registered since AgentOS's
    # in-process agent list isn't itself persisted.
    async with session_scope() as db:
        await sync_agents(db)
    yield
    # The sync engine only actually starts on login (api/auth.py — it
    # needs the workspace PSK, not available until then), so stopping here
    # is a no-op if nobody ever logged in; otherwise it cleanly joins the
    # engine's background trio thread.
    await get_sync_engine().stop()


async def _add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Hive", version="0.1.0", lifespan=lifespan)
    app.include_router(api_router)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_add_security_headers)
    # AgentOS is a Python-level agent registry here, not an HTTP mount —
    # see agentos/service.py's module docstring for why.
    init_agentos()
    engine = init_sync_engine(get_settings().sync_dir)
    engine.set_state_change_handler(handle_incoming_state_change)
    return app


app = create_app()
