"""Entry point.

Runs the App Server; AgentOS (agentos/service.py) and the Sync Engine
(sync/) live in-process inside it rather than as separate supervised child
processes — see docs/architecture.md's "Why not microservices?" section for
why that's the deliberate design, not a stopgap.
"""

import os

import uvicorn

from rivulets.app import app
from rivulets.config import get_settings
from rivulets.update import RESTART_ENV_VAR, cleanup_stale_backup, wait_for_port_free


def main() -> None:
    # docs/security.md's "File permissions" claim (#244): restrict the mode
    # of every file/dir this process creates from here on -- the workspace
    # dir, the databases, backups, synced file blobs, custom tool sources --
    # to owner-only, before any of that gets created below.
    os.umask(0o077)
    settings = get_settings()
    cleanup_stale_backup()
    if os.environ.pop(RESTART_ENV_VAR, None):
        # This process was just spawned by update._spawn_restarted_process
        # handing off from a binary self-update -- wait for the old process
        # to actually release the port before racing it for the same bind.
        wait_for_port_free(settings.app_server_host, settings.app_server_port)
    if settings.app_server_host not in ("127.0.0.1", "localhost", "0.0.0.0"):  # noqa: S104
        # NFR-3.4: refuse to bind anywhere but localhost by default — the
        # default (config.py) stays 127.0.0.1, so this only fires if
        # something explicitly set RIVULETS_APP_SERVER_HOST to a value it
        # doesn't recognize.
        #
        # 0.0.0.0 is the one deliberate exception, for the Docker image's
        # entrypoint: inside a container, the app's own bind address and the
        # machine's actual exposure are two different layers. 127.0.0.1
        # would only be reachable from inside the container's own network
        # namespace — `docker run -p` forwards to the container's eth0, not
        # its loopback — so 0.0.0.0 here is what lets the *host's* -p flag
        # be the one enforcing NFR-3.4's intent (see docker-compose.yml and
        # README.md#docker, both of which publish 127.0.0.1:8484:8484 by
        # default: loopback-only on the host, same guarantee as a native
        # install, unless a user explicitly opts into wider exposure there).
        raise SystemExit(
            f"Refusing to start: app_server_host={settings.app_server_host!r} "
            "must be 127.0.0.1, localhost, or 0.0.0.0 (see NFR-3.4)."
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
