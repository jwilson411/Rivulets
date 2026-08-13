"""FastAPI app factory (see docs/architecture.md).

Security posture (see docs/security.md): same-origin only (the UI and API
share this process), no CORS, and a CSP that only trusts this origin. The
App Server binds to 127.0.0.1 — enforced in main.py, not here, since
that's a listen-address concern rather than an app concern.
"""

import asyncio
import base64
import contextlib
import hashlib
import re
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.middleware.base import BaseHTTPMiddleware

from rivulets.agentos import init_agentos, sync_agents
from rivulets.agentos.tool_resolution import seed_builtin_tools
from rivulets.api import api_router
from rivulets.backup import run_startup_backup_checks
from rivulets.config import get_settings
from rivulets.db.session import get_engine, init_db, session_scope
from rivulets.dispatch import invoke_agent_remotely
from rivulets.sync import get_sync_engine, init_sync_engine
from rivulets.sync.apply import handle_incoming_state_change
from rivulets.sync.capabilities import load_capabilities
from rivulets.sync.publish import drain_pending_outbound
from rivulets.tracing import run_retention_loop
from rivulets.version import APP_VERSION
from rivulets.workflows.scheduler import run_scheduler_loop

_BASE_CSP = "default-src 'self'; connect-src 'self' http://localhost:8484"
_INLINE_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def _build_csp(static_dir: Path | None) -> str:
    """The UI's departures from strict same-origin, all forced by
    @sveltejs/adapter-static's fixed output or SvelteKit's client runtime —
    none are anything ui/ code opted into:

    1. adapter-static's index.html always boots the client via one inline
       <script> (its content — including a per-build-random SvelteKit
       instance id — is fixed by the adapter, not something ui/ can opt out
       of), so 'unsafe-inline' would be the only alternative to hashing it
       here. The hash is recomputed from whatever actually got built rather
       than pinned, since that id changes every build.
    2. SvelteKit's bundled client runtime applies at least one inline style
       and one data: URI image (its built-in error-overlay chrome) during
       normal boot, not just on an actual error — unlike #1 this isn't a
       single static string to hash, so this is 'unsafe-inline' for
       style-src specifically. Scoped to styles only: the far lower-severity
       half of the two, and script-src stays hash-pinned.
    3. Source Serif 4 loads from Google Fonts (ui/src/routes/layout.css) —
       the one CDN dependency in an otherwise fully local-first app. Kept as
       a CDN load rather than vendoring the font files (open in ui/#123 to
       self-host and drop this exception); it degrades to a system serif
       offline, it does not break the app.

    All of these only apply when a UI build is actually being served — an
    API-only server (no static_dir) has no inline script, style, or image to
    allow.
    """
    if static_dir is None:
        return f"{_BASE_CSP}; script-src 'self'"
    index_html = (static_dir / "index.html").read_text()
    match = _INLINE_SCRIPT_RE.search(index_html)
    script_src = "'self'"
    if match is not None:
        digest = hashlib.sha256(match.group(1).encode()).digest()
        script_src += f" 'sha256-{base64.b64encode(digest).decode()}'"
    return (
        f"{_BASE_CSP}; script-src {script_src}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:"
    )


def _static_dir() -> Path | None:
    """Where the built UI (ui/build, adapter-static + fallback: 'index.html')
    lands relative to this module, in both runtime shapes:

    - Frozen (PyInstaller onefile): packaging/_common.py bundles ui/build as
      data at "rivulets/static", which onefile extracts under sys._MEIPASS.
    - Unfrozen (uv run / installed package): no bundling step exists, so this
      only resolves if something has copied ui/build to server/src/rivulets/
      static by hand — harmless when absent, see the None case below.

    Returns None if `npm run build` (or the copy, in the unfrozen case)
    hasn't happened — the app.py caller then runs API-only, matching
    packaging/_common.py's own comment on the equivalent PyInstaller-time
    check.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base = (
        Path(meipass) / "rivulets" / "static"
        if meipass
        else Path(__file__).resolve().parent / "static"
    )
    return base if base.is_dir() else None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await init_db()
    # security-and-dr.md's two startup-time triggers: a pre-upgrade
    # snapshot (only if the binary version changed since last start) and
    # the daily automatic backup. Both are internally idempotent/failure-
    # tolerant (see backup.py), so this never blocks the rest of startup.
    await run_startup_backup_checks(get_settings(), get_engine())
    async with session_scope() as db:
        # Builtin tool rows (FR-8.2) must exist before sync_agents() builds
        # any agent that might reference one via the agent_tool join table.
        await seed_builtin_tools(db)
        # Repopulate AgentOS's agent list from the DB on every startup —
        # agents created before a restart need to be re-registered since
        # AgentOS's in-process agent list isn't itself persisted.
        await sync_agents(db)
    # #92: the app's first plain-asyncio recurring background task — see
    # workflows/scheduler.py's module docstring for why this doesn't need
    # the sync engine's trio-on-a-thread machinery below.
    scheduler_task = asyncio.create_task(run_scheduler_loop())
    # #96: same pattern, for run-trace retention (tracing.py's module
    # docstring) — traces accumulate continuously, unlike backup.py's
    # startup-only snapshot pruning above.
    retention_task = asyncio.create_task(run_retention_loop())
    yield
    scheduler_task.cancel()
    retention_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await scheduler_task
    with contextlib.suppress(asyncio.CancelledError):
        await retention_task
    # The sync engine only actually starts on login (api/auth.py — it
    # needs the workspace PSK, not available until then), so stopping here
    # is a no-op if nobody ever logged in; otherwise it cleanly joins the
    # engine's background trio thread.
    await get_sync_engine().stop()


async def _add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = request.app.state.csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def _integrity_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    # #250 safety net: a route-level pre-check (api/agents.py's
    # _check_name_available, api/workflows.py's inline equivalent) closes
    # the common case for the handful of routes that have one, but any
    # other UniqueConstraint/ForeignKey violation would otherwise surface
    # here as an unhandled 500 carrying the raw SQLAlchemy/SQLite
    # statement (params included) in the body. A stable `error` shape
    # keeps that text out of the response for every route, not just the
    # ones that got a dedicated fix.
    return JSONResponse(
        {"error": {"code": "conflict", "message": "That change conflicts with existing data."}},
        status_code=status.HTTP_409_CONFLICT,
    )


async def _operational_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    # Same reasoning as _integrity_error_handler, for SQLite's "database is
    # locked" (a concurrent writer holding the file lock) -- retryable, so
    # 503 rather than 409's "this won't succeed as-is".
    return JSONResponse(
        {
            "error": {
                "code": "database_unavailable",
                "message": "The database is temporarily busy, try again.",
            }
        },
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def create_app() -> FastAPI:
    # docs_url/redoc_url/openapi_url off: this API has no external
    # consumers to document for, and leaving Swagger/ReDoc/the schema
    # reachable is unauthenticated surface for no product benefit — every
    # other route here sits behind the workspace JWT (see docs/security.md).
    app = FastAPI(
        title="Rivulets",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Registered before the static/SPA-fallback route below so its 404s stay
    # 404s (see _spa_fallback's own "api/" guard for the other half of that).
    app.include_router(api_router)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
    app.add_exception_handler(OperationalError, _operational_error_handler)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_add_security_headers)
    # AgentOS is a Python-level agent registry here, not an HTTP mount —
    # see agentos/service.py's module docstring for why.
    init_agentos()
    engine = init_sync_engine(get_settings().sync_dir)
    engine.set_state_change_handler(handle_incoming_state_change)
    engine.set_peer_connected_handler(_on_peer_connected)
    engine.set_agent_dispatch_handler(invoke_agent_remotely)

    static_dir = _static_dir()
    app.state.csp = _build_csp(static_dir)
    if static_dir is not None:
        _mount_ui(app, static_dir)
    return app


def _mount_ui(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA for everything the API router didn't already
    claim: a real static asset (SvelteKit's hashed /_app/... bundle, favicon,
    etc.) if the path matches one on disk, else static_dir's index.html so
    the client-side router (ui/src/routes/+layout.ts, ssr = false) can take
    over — e.g. a hard refresh on /channels/<id>."""

    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            # Fell through api_router because it's a genuinely unknown API
            # route — keep that a JSON-shaped 404, not the SPA's index.html.
            raise HTTPException(status_code=404)
        candidate = (static_dir / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")

    # add_api_route (rather than the @app.get(...) decorator form) so this
    # closure counts as "used" to pyright's reportUnusedFunction — it can't
    # see decorator-registered handlers as reachable on their own.
    app.add_api_route("/{full_path:path}", spa_fallback, methods=["GET"])


async def _on_peer_connected() -> None:
    async with session_scope() as db:
        await drain_pending_outbound(db)
    # Issue #10: re-announce this node's own capability tags to every newly
    # connected peer -- there's no "resync everything on connect" mechanism
    # in this codebase (see set_peer_connected_handler's own docstring for
    # why drain_pending_outbound needs the same delayed-retry treatment),
    # so a fresh broadcast per connection is how a peer that joined after
    # tags were set still learns about them.
    await get_sync_engine().publish_capabilities(load_capabilities(get_settings().sync_dir))


app = create_app()
