"""Entry point.

The full design (docs/infrastructure/deployment-and-networking.md) runs a
process supervisor over three children: App Server, AgentOS, and the Sync
Engine, with crash-loop detection and graceful shutdown. AgentOS and the
Sync Engine aren't wired up yet (see their respective TODOs), so this
currently just runs the App Server directly. Swap this for the real
supervisor once those two children exist to manage.
"""

import uvicorn

from agent_hive.app import app
from agent_hive.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.app_server_host not in ("127.0.0.1", "localhost"):
        # NFR-3.4: refuse to bind anywhere but localhost by default.
        raise SystemExit(
            f"Refusing to start: app_server_host={settings.app_server_host!r} "
            "must be 127.0.0.1 (see NFR-3.4)."
        )
    settings.ensure_workspace_dirs()
    # Pass the app object directly rather than the "module:attr" import
    # string uvicorn normally takes — the string form re-imports via
    # importlib at startup, which breaks inside a PyInstaller onefile
    # binary. Passing the object also means --reload isn't available here,
    # which is fine: reload is a dev-only workflow (see server/README.md),
    # never used from this entry point.
    uvicorn.run(
        app,
        host=settings.app_server_host,
        port=settings.app_server_port,
    )


if __name__ == "__main__":
    main()
