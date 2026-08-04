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

from agent_hive.api import api_router
from agent_hive.db.session import init_db

_CSP = "default-src 'self'; script-src 'self'; connect-src 'self' http://localhost:8484"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    await init_db()
    yield


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
    return app


app = create_app()
